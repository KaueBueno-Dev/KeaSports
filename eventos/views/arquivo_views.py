from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from eventos.models import ArquivoExcel, Corredor, Corrida, PercursoCorrida

from ..filters import CorredorFilter, categoria_sem_sexo


def _corrida_do_arquivo(arquivo):
    if arquivo.corrida_id:
        return arquivo.corrida
    if arquivo.percurso_id:
        return arquivo.percurso.corrida
    return None


def _arquivos_da_corrida(corrida):
    return (
        ArquivoExcel.objects
        .select_related("corrida", "percurso__corrida")
        .filter(Q(corrida=corrida) | Q(percurso__corrida=corrida))
        .order_by("-criado_em", "-id")
    )


def _arquivo_principal_da_corrida(corrida):
    return _arquivos_da_corrida(corrida).first()


def _resultados_unicos_por_corrida(query=""):
    arquivos = (
        ArquivoExcel.objects
        .select_related("corrida", "percurso__corrida")
        .all()
        .order_by("-criado_em", "-id")
    )

    if query:
        arquivos = arquivos.filter(
            Q(nome__icontains=query)
            | Q(corrida__nome__icontains=query)
            | Q(percurso__nome__icontains=query)
            | Q(percurso__corrida__nome__icontains=query)
        )

    resultados = []
    corridas_vistas = set()
    for arquivo in arquivos:
        corrida = _corrida_do_arquivo(arquivo)
        if not corrida or corrida.id in corridas_vistas:
            continue
        arquivo.corrida_resultado_publico = corrida
        resultados.append(arquivo)
        corridas_vistas.add(corrida.id)
    return resultados


def _corredores_da_corrida(corrida):
    return (
        Corredor.objects
        .select_related("arquivo", "arquivo__corrida", "arquivo__percurso__corrida", "participante")
        .filter(Q(arquivo__corrida=corrida) | Q(arquivo__percurso__corrida=corrida))
    )


def _distancias_do_resultado(corredores):
    distancias = []
    vistas = set()
    for distancia in (
        corredores
        .values_list("distancia", flat=True)
        .order_by("distancia")
        .distinct()
    ):
        nome = (distancia or "Geral").strip() or "Geral"
        if nome not in vistas:
            distancias.append(nome)
            vistas.add(nome)
    return distancias


def ArquivoExcelListView(request):
    query = request.GET.get("q", "").strip()
    resultados = _resultados_unicos_por_corrida(query)

    paginator = Paginator(resultados, 9)
    page_number = request.GET.get("page")
    page = paginator.get_page(page_number)

    return render(request, "eventos/arquivo/arquivo_list.html", {
        "arquivos": page,
        "page": page,
        "query": query,
    })


def _render_resultado_corrida(request, corrida, arquivo=None, distancia_forcada=None):
    arquivo = arquivo or _arquivo_principal_da_corrida(corrida)
    corredores = _corredores_da_corrida(corrida).order_by("colocacao", "id")
    distancias = _distancias_do_resultado(corredores)
    distancia_selecionada = (distancia_forcada or request.GET.get("distancia", "")).strip()

    if len(distancias) == 1:
        distancia_selecionada = distancias[0]
    elif len(distancias) > 1:
        if not distancia_selecionada or distancia_selecionada not in distancias:
            distancia_selecionada = distancias[0]
        corredores = corredores.filter(distancia=distancia_selecionada)

    filtro = CorredorFilter(request.GET, queryset=corredores)
    filtro_final = filtro.qs.order_by("tempo_segundos", "colocacao", "id")
    for i, corredor in enumerate(filtro_final, start=1):
        corredor.colocacao_exibicao = i

    paginator = Paginator(filtro_final, 10)
    page_number = request.GET.get("page")
    page = paginator.get_page(page_number)
    for corredor in page.object_list:
        corredor.categoria_exibicao = categoria_sem_sexo(corredor.categoria)

    return render(request, "eventos/arquivo/arquivo_detail.html", {
        "arquivo": arquivo,
        "corrida": corrida,
        "percurso": None,
        "distancias": distancias,
        "distancia_selecionada": distancia_selecionada,
        "mostrar_filtro_distancia": len(distancias) > 1,
        "page": page,
        "filter": filtro,
    })


def arquivo_detail(request, pk):
    arquivo = get_object_or_404(
        ArquivoExcel.objects.select_related("corrida", "percurso__corrida"),
        pk=pk,
    )
    corrida = _corrida_do_arquivo(arquivo)
    if not corrida:
        return _render_resultado_corrida(request, arquivo.corrida, arquivo)
    return _render_resultado_corrida(request, corrida, arquivo)


def corrida_resultados(request, corrida_id):
    corrida = get_object_or_404(Corrida, pk=corrida_id)
    return _render_resultado_corrida(request, corrida)


def resultado_percurso_detail(request, corrida_id, percurso_id):
    percurso = get_object_or_404(
        PercursoCorrida.objects.select_related("corrida"),
        pk=percurso_id,
        corrida_id=corrida_id,
    )
    return _render_resultado_corrida(request, percurso.corrida, distancia_forcada=percurso.nome)
