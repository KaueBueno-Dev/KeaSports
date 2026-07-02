import logging
import re
from dataclasses import dataclass

import requests
from django.conf import settings
from django.utils import timezone
from requests import RequestException


logger = logging.getLogger(__name__)
MAX_ERROR_LENGTH = 500


@dataclass(frozen=True)
class CronometragemSendResult:
    sent: bool = False
    skipped: bool = False
    error: str = ""


def _texto(valor):
    return str(valor or "").strip()


def _formatar_categoria(categoria):
    categoria = _texto(categoria)
    if not categoria:
        return ""
    if " anos" in categoria or " a " in categoria:
        return categoria
    if categoria.endswith("+"):
        return f"{categoria} anos"

    match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", categoria)
    if match:
        return f"{match.group(1)} a {match.group(2)} anos"

    return categoria


def _formatar_percurso_nome(percurso):
    if percurso is None:
        return ""

    distancia_km = getattr(percurso, "distancia_km", None)
    if distancia_km not in (None, ""):
        distancia = format(distancia_km, "f").rstrip("0").rstrip(".")
        if distancia:
            return f"{distancia} Km"

    return _texto(getattr(percurso, "nome", ""))


def montar_payload_inscricao(inscricao):
    participante = inscricao.participante

    return {
        "inscricao_id": inscricao.id,
        "evento_id": inscricao.corrida_id,
        "evento_nome": _texto(inscricao.corrida.nome),
        "percurso_id": inscricao.percurso_id,
        "percurso_nome": _formatar_percurso_nome(inscricao.percurso if inscricao.percurso_id else None),
        "atleta_id": inscricao.participante_id,
        "nome": _texto(participante.nome),
        "sexo": _texto(participante.get_sexo_display() if participante.sexo else ""),
        "categoria": _formatar_categoria(participante.categoria),
        "cidade": _texto(participante.cidade),
        "equipe": _texto(participante.equipe),
    }


def _mensagem_segura(mensagem):
    mensagem = _texto(mensagem)
    api_key = getattr(settings, "CRONOMETRAGEM_RECEIVER_API_KEY", "")
    if api_key:
        mensagem = mensagem.replace(api_key, "[redacted]")
    if len(mensagem) > MAX_ERROR_LENGTH:
        return f"{mensagem[:MAX_ERROR_LENGTH - 3]}..."
    return mensagem


def _registrar_sucesso(inscricao_id):
    from eventos.models import Inscricao

    agora = timezone.now()
    Inscricao.objects.filter(pk=inscricao_id).update(
        enviada_cronometragem=True,
        data_envio_cronometragem=agora,
        erro_envio_cronometragem="",
        atualizada_em=agora,
    )


def _registrar_falha(inscricao_id, erro):
    from eventos.models import Inscricao

    agora = timezone.now()
    Inscricao.objects.filter(pk=inscricao_id).update(
        enviada_cronometragem=False,
        data_envio_cronometragem=None,
        erro_envio_cronometragem=_mensagem_segura(erro),
        atualizada_em=agora,
    )


def enviar_inscricao_para_cronometragem(inscricao_id, permitir_reenvio=False):
    from eventos.models import Inscricao

    try:
        inscricao = (
            Inscricao.objects
            .select_related("participante", "corrida", "percurso")
            .get(pk=inscricao_id)
        )
    except Inscricao.DoesNotExist:
        return CronometragemSendResult(skipped=True, error="Inscricao nao encontrada.")

    if not inscricao.pago:
        return CronometragemSendResult(skipped=True, error="Inscricao nao paga.")
    if inscricao.enviada_cronometragem and not permitir_reenvio:
        return CronometragemSendResult(skipped=True, error="Inscricao ja enviada.")

    receiver_url = getattr(settings, "CRONOMETRAGEM_RECEIVER_URL", "")
    api_key = getattr(settings, "CRONOMETRAGEM_RECEIVER_API_KEY", "")
    timeout = getattr(settings, "CRONOMETRAGEM_RECEIVER_TIMEOUT", 3)

    if not receiver_url or not api_key:
        erro = "Configuracao de envio para cronometragem incompleta."
        _registrar_falha(inscricao.id, erro)
        logger.warning(
            "Inscricao %s pendente para cronometragem: %s",
            inscricao.id,
            erro,
        )
        return CronometragemSendResult(error=erro)

    try:
        response = requests.post(
            receiver_url,
            json=montar_payload_inscricao(inscricao),
            headers={"Authorization": f"Api-Key {api_key}"},
            timeout=timeout,
        )
    except RequestException as exc:
        erro = _mensagem_segura(f"{exc.__class__.__name__}: {exc}")
        _registrar_falha(inscricao.id, erro)
        logger.warning(
            "Falha ao enviar inscricao %s para cronometragem: %s",
            inscricao.id,
            erro,
        )
        return CronometragemSendResult(error=erro)

    if not 200 <= response.status_code < 300:
        corpo = _texto(getattr(response, "text", ""))
        erro = f"HTTP {response.status_code}"
        if corpo:
            erro = f"{erro}: {corpo}"
        erro = _mensagem_segura(erro)
        _registrar_falha(inscricao.id, erro)
        logger.warning(
            "FastAPI recusou inscricao %s para cronometragem: %s",
            inscricao.id,
            erro,
        )
        return CronometragemSendResult(error=erro)

    _registrar_sucesso(inscricao.id)
    return CronometragemSendResult(sent=True)



