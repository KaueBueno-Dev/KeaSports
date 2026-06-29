import re
import unicodedata
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from eventos.forms import ParticipanteForm
from eventos.models import ArquivoExcel, Corredor, Corrida, Inscricao, Participante


RANKINKG_CATEGORIAS = [
    "15 - 19",
    "20 - 24",
    "25 - 29",
    "30 - 34",
    "35 - 39",
    "40 - 44",
    "45 - 49",
    "50 - 54",
    "55 - 59",
    "60 - 64",
    "65+",
]
RANKINKG_SEXOS = [
    ("M", "Masculino"),
    ("F", "Feminino"),
]
PONTOS_GERAL = [21, 18, 16, 14, 12]
PONTOS_CATEGORIA = [10, 8, 6, 4, 2, 1, 1, 1, 1, 1]


def index(request):
    corridas = Corrida.objects.all().order_by("-data")[:3]
    arquivos_recentes = (
        ArquivoExcel.objects
        .select_related("corrida", "percurso__corrida")
        .all()
        .order_by("-criado_em")
    )
    resultados_recentes = []
    corridas_vistas = set()
    for arquivo in arquivos_recentes:
        corrida = arquivo.corrida or (arquivo.percurso.corrida if arquivo.percurso_id else None)
        if not corrida or corrida.id in corridas_vistas:
            continue
        arquivo.corrida_resultado_publico = corrida
        resultados_recentes.append(arquivo)
        corridas_vistas.add(corrida.id)
        if len(resultados_recentes) == 9:
            break

    return render(
        request,
        "eventos/index.html",
        {
            "corridas": corridas,
            "resultados_recentes": resultados_recentes,
        },
    )


def listar_corridas(request):
    corridas = Corrida.objects.all()
    return render(request, "eventos/corrida/listar_corridas.html", {"corridas": corridas})


def _normalizar_texto(valor):
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(char for char in texto if not unicodedata.combining(char))


def _normalizar_categoria(valor):
    texto = _normalizar_texto(valor)

    if "65" in texto and ("+" in texto or "mais" in texto):
        return "65+"

    match = re.search(r"(\d{2})\s*[-a/]\s*(\d{2})", texto)
    if not match:
        match = re.search(r"(\d{2})(\d{2})", texto)
    if not match:
        return ""

    categoria = f"{match.group(1)} - {match.group(2)}"
    return categoria if categoria in RANKINKG_CATEGORIAS else ""


def _normalizar_sexo(corredor):
    if corredor.participante and corredor.participante.sexo in {"M", "F"}:
        return corredor.participante.sexo

    categoria = _normalizar_texto(corredor.categoria)
    if re.search(r"\b(f|fem|feminino)\b", categoria) or categoria.startswith("f"):
        return "F"
    if re.search(r"\b(m|masc|masculino)\b", categoria) or categoria.startswith("m"):
        return "M"
    return ""


def _chave_atleta(corredor, sexo):
    if corredor.participante_id:
        return ("participante", corredor.participante_id)
    return ("nome", _normalizar_texto(corredor.nome), sexo)


def _novo_atleta(corredor, sexo, categoria):
    return {
        "nome": corredor.nome,
        "sexo": sexo,
        "categoria": categoria or _normalizar_categoria(corredor.categoria) or corredor.categoria,
        "pontos": 0,
    }


def _somar_pontos(ranking, corredor, sexo, categoria, pontos):
    chave = _chave_atleta(corredor, sexo)
    atleta = ranking.setdefault(chave, _novo_atleta(corredor, sexo, categoria))
    atleta["pontos"] += pontos
    if categoria and not atleta.get("categoria"):
        atleta["categoria"] = categoria
    return atleta


def _rankear_atletas(atletas, limite=None):
    ranking = sorted(
        atletas,
        key=lambda atleta: (-atleta["pontos"], atleta["nome"]),
    )
    return ranking[:limite] if limite else ranking


def _ordenar_resultados(corredores):
    return sorted(
        corredores,
        key=lambda c: (
            c.tempo_segundos is None,
            c.tempo_segundos if c.tempo_segundos is not None else c.colocacao,
            c.colocacao,
        ),
    )


def _agrupar_por_sexo(corredores):
    corredores_por_sexo = {"M": [], "F": []}

    for corredor in corredores:
        sexo = _normalizar_sexo(corredor)
        if sexo in corredores_por_sexo:
            corredores_por_sexo[sexo].append(corredor)

    return corredores_por_sexo


def _pontuar_geral_por_sexo(corredores_por_sexo, ranking_geral):
    top_geral_ids_por_sexo = {"M": set(), "F": set()}

    for sexo, corredores_sexo in corredores_por_sexo.items():
        for indice, corredor in enumerate(_ordenar_resultados(corredores_sexo)[:5]):
            categoria = _normalizar_categoria(corredor.categoria)
            _somar_pontos(
                ranking_geral[sexo],
                corredor,
                sexo,
                categoria,
                PONTOS_GERAL[indice],
            )
            top_geral_ids_por_sexo[sexo].add(corredor.id)

    return top_geral_ids_por_sexo


def _pontuar_categorias_por_sexo(corredores, ranking_categoria, top_geral_ids_por_sexo):
    grupos_categoria = defaultdict(list)

    for corredor in corredores:
        sexo = _normalizar_sexo(corredor)
        categoria = _normalizar_categoria(corredor.categoria)
        if sexo in {"M", "F"} and categoria:
            grupos_categoria[(categoria, sexo)].append(corredor)

    for (categoria, sexo), corredores_categoria in grupos_categoria.items():
        elegiveis = [
            corredor
            for corredor in _ordenar_resultados(corredores_categoria)
            if corredor.id not in top_geral_ids_por_sexo[sexo]
        ]

        for indice, corredor in enumerate(elegiveis[:10]):
            _somar_pontos(
                ranking_categoria[categoria][sexo],
                corredor,
                sexo,
                categoria,
                PONTOS_CATEGORIA[indice],
            )


def calcular_rankinkg():
    ranking_geral = {
        "M": defaultdict(dict),
        "F": defaultdict(dict),
    }
    ranking_categoria = defaultdict(lambda: defaultdict(dict))

    arquivos_ids = (
        Corredor.objects
        .exclude(arquivo_id__isnull=True)
        .values_list("arquivo_id", flat=True)
        .distinct()
    )

    for arquivo_id in arquivos_ids:
        corredores = list(
            Corredor.objects
            .select_related("participante")
            .filter(arquivo_id=arquivo_id)
            .order_by("colocacao")
        )
        corredores_por_sexo = _agrupar_por_sexo(corredores)
        top_geral_ids_por_sexo = _pontuar_geral_por_sexo(
            corredores_por_sexo,
            ranking_geral,
        )
        _pontuar_categorias_por_sexo(
            corredores,
            ranking_categoria,
            top_geral_ids_por_sexo,
        )

    gerais = {}
    for sexo, atletas in ranking_geral.items():
        gerais[sexo] = _rankear_atletas(atletas.values(), limite=5)

    categorias = {}
    for categoria in RANKINKG_CATEGORIAS:
        categorias[categoria] = {}
        for sexo, _ in RANKINKG_SEXOS:
            categorias[categoria][sexo] = _rankear_atletas(
                ranking_categoria[categoria][sexo].values()
            )

    return gerais, categorias


def rankinkg(request):
    categoria_selecionada = request.GET.get("categoria") or RANKINKG_CATEGORIAS[0]
    sexo_selecionado = request.GET.get("sexo") or "M"

    if categoria_selecionada not in RANKINKG_CATEGORIAS:
        categoria_selecionada = RANKINKG_CATEGORIAS[0]
    if sexo_selecionado not in {"M", "F"}:
        sexo_selecionado = "M"

    ranking_geral, ranking_categoria = calcular_rankinkg()
    ranking_filtrado = ranking_categoria[categoria_selecionada][sexo_selecionado]

    return render(
        request,
        "eventos/rankinkg.html",
        {
            "categorias": RANKINKG_CATEGORIAS,
            "sexos": RANKINKG_SEXOS,
            "categoria_selecionada": categoria_selecionada,
            "sexo_selecionado": sexo_selecionado,
            "sexo_selecionado_nome": dict(RANKINKG_SEXOS)[sexo_selecionado],
            "ranking_geral_masculino": ranking_geral["M"],
            "ranking_geral_feminino": ranking_geral["F"],
            "ranking_categoria": ranking_filtrado,
            "tem_resultados": Corredor.objects.exists(),
            "pontos_geral": zip(range(1, 6), PONTOS_GERAL),
            "pontos_categoria": [
                ("1º", 10),
                ("2º", 8),
                ("3º", 6),
                ("4º", 4),
                ("5º", 2),
                ("6º ao 10º", 1),
            ],
        },
    )


def validar_cpf(cpf):
    cpf = re.sub(r"\D", "", cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    digito1 = (soma * 10) % 11
    if digito1 == 10:
        digito1 = 0
    if digito1 != int(cpf[9]):
        return False

    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    digito2 = (soma * 10) % 11
    if digito2 == 10:
        digito2 = 0
    if digito2 != int(cpf[10]):
        return False
    return True


@login_required
def buscar_usuario_cpf(request):
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.GET.get("cpf"):
        cpf = request.GET.get("cpf", "").strip()
        cpf_limpo = re.sub(r"\D", "", cpf)

        if not validar_cpf(cpf_limpo):
            return JsonResponse({"encontrado": False, "erro": "CPF invalido."})

        if not request.user.is_staff and cpf_limpo != request.user.username:
            return JsonResponse(
                {"encontrado": False, "erro": "CPF nao autorizado para este usuario."},
                status=403,
            )

        try:
            participante = Participante.objects.get(cpf=cpf_limpo)
            dados = {
                "encontrado": True,
                "nome": participante.nome,
                "data_nascimento": participante.data_nascimento.strftime("%Y-%m-%d")
                if participante.data_nascimento
                else "",
                "cidade": participante.cidade or "",
                "equipe": participante.equipe or "",
                "sexo": participante.sexo or "",
                "tamanho_camisa": participante.tamanho_camisa or "M",
            }
            return JsonResponse(dados)
        except Participante.DoesNotExist:
            return JsonResponse(
                {"encontrado": False, "erro": "CPF nao encontrado. Cadastre-se primeiro."}
            )

    return JsonResponse({"encontrado": False, "erro": "Metodo invalido."})


@login_required
def inscrever_corrida(request, corrida_id):
    corrida = get_object_or_404(Corrida, id=corrida_id)
    percursos = list(
        corrida.percursos
        .filter(ativo=True)
        .order_by("ordem", "distancia_km", "nome")
    )
    percurso_error = None

    if request.method == "POST":
        cpf = re.sub(r"\D", "", request.POST.get("cpf", "").strip())
        percurso = None

        if len(percursos) == 1:
            percurso = percursos[0]
        elif len(percursos) > 1:
            percurso_id = request.POST.get("percurso")
            if not percurso_id:
                percurso_error = "Escolha o percurso/distancia da sua inscricao."
            else:
                try:
                    percurso = next(
                        item
                        for item in percursos
                        if str(item.id) == str(percurso_id)
                    )
                except StopIteration:
                    percurso_error = "Percurso invalido para esta corrida."
        else:
            percurso_error = "Esta corrida ainda nao possui percurso ativo para inscricao."

        if not cpf:
            cpf = request.user.username

        if not validar_cpf(cpf):
            raise PermissionDenied("CPF invalido.")

        if not request.user.is_staff and cpf != request.user.username:
            raise PermissionDenied("Voce nao pode inscrever outro CPF.")

        try:
            participante_existente = Participante.objects.get(cpf=cpf)
            if (
                participante_existente.usuario
                and participante_existente.usuario != request.user
                and not request.user.is_staff
            ):
                raise PermissionDenied("Participante pertence a outro usuario.")
            form = ParticipanteForm(request.POST, instance=participante_existente)
        except Participante.DoesNotExist:
            form = ParticipanteForm(request.POST)

        if form.is_valid() and not percurso_error:
            with transaction.atomic():
                participante = form.save(commit=False)
                participante.cpf = cpf
                if not participante.usuario_id:
                    participante.usuario = request.user
                participante.save()
                inscricao, criada = Inscricao.objects.get_or_create(
                    participante=participante,
                    corrida=corrida,
                    defaults={"percurso": percurso},
                )
                if not criada and not inscricao.percurso_id:
                    inscricao.percurso = percurso
                    inscricao.save(update_fields=["percurso", "atualizada_em"])
            return render(
                request,
                "eventos/corrida/inscricao_sucesso.html",
                {
                    "corrida": corrida,
                    "participante": participante,
                    "inscricao": inscricao,
                    "percurso": inscricao.percurso,
                    "inscricao_criada": criada,
                },
            )
    else:
        form = ParticipanteForm()

    return render(
        request,
        "eventos/corrida/inscrever_corrida.html",
        {
            "form": form,
            "corrida": corrida,
            "percursos": percursos,
            "percurso_error": percurso_error,
            "buscar_url": "buscar_usuario_cpf",
        },
    )
