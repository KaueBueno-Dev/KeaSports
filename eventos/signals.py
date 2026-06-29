import datetime
import logging
import re
import unicodedata
from decimal import Decimal, InvalidOperation

import pandas as pd
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ArquivoExcel, Corredor


logger = logging.getLogger(__name__)


REQUIRED_COLUMN_ALIASES = {
    "colocacao": ("#", "Colocacao", "Colocação", "Posicao", "Posição"),
    "numero": ("N", "Numero", "Número", "Num", "Bib"),
    "nome": ("Nome", "Atleta", "Nome completo"),
    "categoria": ("Categoria",),
    "tempo": ("Tempo", "Tempo Liquido", "Tempo Líquido", "Tempo Bruto"),
    "velocidade": ("Vel. Média", "Vel. Media", "Velocidade Media", "Velocidade Média"),
}
DISTANCE_COLUMN_ALIASES = (
    "Distancia",
    "distancia",
    "Distância",
    "distância",
    "distancia (Km)",
    "Distância (Km)",
    "Percurso",
    "Prova",
    "Categoria da prova",
    "Categoria Prova",
)


def normalizar_texto(valor):
    texto = str(valor or "").strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", texto).strip().lower()


def normalizar_cabecalho(valor):
    texto = normalizar_texto(valor)
    texto = re.sub(r"\s*\([^)]*\)\s*", "", texto)
    return texto


def normalizar_distancia(valor):
    texto = normalizar_texto(valor).replace(",", ".")
    texto = re.sub(r"\b(km|kms|quilometros|quilometro)\b", "", texto)
    texto = re.sub(r"[^0-9.]+", " ", texto).strip()

    match = re.search(r"\d+(?:\.\d+)?", texto)
    if not match:
        return normalizar_texto(valor)

    try:
        numero = Decimal(match.group(0)).normalize()
    except InvalidOperation:
        return normalizar_texto(valor)

    texto_numero = format(numero, "f")
    if "." in texto_numero:
        texto_numero = texto_numero.rstrip("0").rstrip(".")
    return texto_numero


def find_column(columns, aliases):
    normalized = {normalizar_cabecalho(column): column for column in columns}
    for alias in aliases:
        column = normalized.get(normalizar_cabecalho(alias))
        if column is not None:
            return column
    return None


def parse_tempo_segundos(tempo):
    tempo_str = str(tempo or "").strip()
    if not tempo_str:
        return None, tempo_str

    for fmt in ("%H:%M:%S.%f", "%H:%M:%S"):
        try:
            parsed = datetime.datetime.strptime(tempo_str, fmt)
            return (
                parsed.hour * 3600
                + parsed.minute * 60
                + parsed.second
                + parsed.microsecond / 1e6
            ), tempo_str
        except ValueError:
            continue

    return None, tempo_str


def corrida_do_arquivo(arquivo):
    if arquivo.corrida_id:
        return arquivo.corrida
    if arquivo.percurso_id:
        return arquivo.percurso.corrida
    return None


def percurso_keys(percurso):
    keys = {normalizar_texto(percurso.nome), normalizar_distancia(percurso.nome)}
    if percurso.distancia_km is not None:
        keys.add(normalizar_distancia(percurso.distancia_km))
    return {key for key in keys if key}


def mapa_percursos_corrida(corrida):
    if not corrida:
        return {}

    mapa = {}
    for percurso in corrida.percursos.all():
        for key in percurso_keys(percurso):
            mapa[key] = percurso
    return mapa


def obter_percurso_linha(row, distance_column, arquivo, percurso_map):
    if distance_column and str(row.get(distance_column, "")).strip():
        distancia_excel = row.get(distance_column, "")
        key = normalizar_distancia(distancia_excel)
        percurso = percurso_map.get(key) or percurso_map.get(normalizar_texto(distancia_excel))
        if not percurso:
            opcoes = ", ".join(sorted({percurso.nome for percurso in percurso_map.values()}))
            raise ValueError(
                f"Distancia '{distancia_excel}' nao existe como percurso cadastrado nesta corrida. "
                f"Percursos cadastrados: {opcoes or 'nenhum'}."
            )
        return percurso.nome

    if arquivo.percurso_id:
        return arquivo.percurso.nome

    if percurso_map:
        opcoes = ", ".join(sorted({percurso.nome for percurso in percurso_map.values()}))
        raise ValueError(
            "O Excel nao possui coluna Distancia e a corrida possui percursos cadastrados. "
            f"Informe uma distancia correspondente a um destes percursos: {opcoes}."
        )

    return "Geral"


@receiver(post_save, sender=ArquivoExcel)
def extrair_dados_excel(sender, instance, created, **kwargs):
    if not created:
        return

    try:
        with instance.arquivo.open("rb") as arquivo:
            df = pd.read_excel(arquivo)
        df.columns = df.columns.str.strip()

        columns = list(df.columns)
        column_map = {
            key: find_column(columns, aliases)
            for key, aliases in REQUIRED_COLUMN_ALIASES.items()
        }
        missing_columns = [key for key, column in column_map.items() if column is None]
        if missing_columns:
            raise ValueError(f"Colunas obrigatorias ausentes: {', '.join(sorted(missing_columns))}.")

        distance_column = find_column(columns, DISTANCE_COLUMN_ALIASES)
        percurso_map = mapa_percursos_corrida(corrida_do_arquivo(instance))

        colocacao_column = column_map["colocacao"]
        if colocacao_column in df.columns:
            df = df.sort_values(by=colocacao_column, ascending=True)

        corredores = []
        for _, row in df.iterrows():
            colocacao_str = (
                str(row.get(colocacao_column, ""))
                .replace("º", "")
                .replace("Âº", "")
                .replace("Ã‚Âº", "")
                .strip()
            )
            try:
                colocacao_num = int(colocacao_str)
            except (TypeError, ValueError):
                logger.warning(
                    "Linha ignorada no ArquivoExcel %s: colocacao invalida %r",
                    instance.pk,
                    colocacao_str,
                )
                continue

            tempo_segundos, tempo_formatado = parse_tempo_segundos(row.get(column_map["tempo"], ""))

            corredores.append(
                Corredor(
                    arquivo=instance,
                    colocacao=colocacao_num,
                    numero=row.get(column_map["numero"], ""),
                    nome=row.get(column_map["nome"], ""),
                    categoria=row.get(column_map["categoria"], ""),
                    distancia=obter_percurso_linha(row, distance_column, instance, percurso_map),
                    equipe=row.get("Equipe", ""),
                    tempo_segundos=tempo_segundos,
                    tempo_formatado=tempo_formatado,
                    Vel_media=row.get(column_map["velocidade"], ""),
                )
            )

        if corredores:
            with transaction.atomic():
                Corredor.objects.bulk_create(corredores, batch_size=500)

    except Exception:
        logger.exception("Falha ao processar ArquivoExcel %s", instance.pk)
        raise
