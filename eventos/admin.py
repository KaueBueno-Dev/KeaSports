import sys

from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from eventos.forms import ArquivoExcelAdminForm, InscricaoAdminForm
from eventos.models import (
    ArquivoExcel,
    Corredor,
    Corrida,
    Inscricao,
    Participante,
    PercursoCorrida,
    ResultadoInscricao,
)


ADMIN_MODEL_NAMES = (
    (Corrida, "Evento", "Eventos"),
    (PercursoCorrida, "Distância", "Distâncias"),
    (Participante, "Atleta", "Atletas"),
    (ResultadoInscricao, "Resultado da Cronometragem", "Resultados da Cronometragem"),
    (Corredor, "Classificação", "Classificações"),
)

ADMIN_MODEL_ORDER = {
    "Corrida": 0,
    "PercursoCorrida": 1,
    "Participante": 2,
    "Inscricao": 3,
    "ArquivoExcel": 4,
    "ResultadoInscricao": 5,
    "Corredor": 6,
}


def apply_admin_model_names():
    for model, verbose_name, verbose_name_plural in ADMIN_MODEL_NAMES:
        model._meta.verbose_name = verbose_name
        model._meta.verbose_name_plural = verbose_name_plural


def apply_admin_menu_order():
    if getattr(admin.site, "_kea_menu_order_applied", False):
        return

    original_get_app_list = admin.site.get_app_list

    def get_app_list(request, app_label=None):
        app_list = original_get_app_list(request, app_label)
        for app in app_list:
            if app["app_label"] == "api_s":
                app["models"].sort(
                    key=lambda model: ADMIN_MODEL_ORDER.get(model["object_name"], 99)
                )
        return app_list

    admin.site.get_app_list = get_app_list
    admin.site._kea_menu_order_applied = True


if "makemigrations" not in sys.argv:
    apply_admin_model_names()
    apply_admin_menu_order()


class PercursoCorridaInline(admin.TabularInline):
    model = PercursoCorrida
    extra = 1
    fields = ("nome", "distancia_km", "ativo", "ordem", "tem_resultado")
    readonly_fields = ("tem_resultado",)

    def tem_resultado(self, obj):
        if not obj.pk:
            return "-"
        return "Sim" if hasattr(obj, "resultado") else "Nao"

    tem_resultado.short_description = "Resultado"


class ResultadoPercursoInline(admin.StackedInline):
    model = ArquivoExcel
    extra = 0
    max_num = 1
    fields = ("nome", "data_corrida", "local", "arquivo", "imagem", "criado_em")
    readonly_fields = ("criado_em",)


class InscricaoInline(admin.TabularInline):
    model = Inscricao
    form = InscricaoAdminForm
    extra = 1
    autocomplete_fields = ("participante",)
    fields = (
        "participante",
        "percurso",
        "pago",
        "numero_chip",
        "criada_em",
    )
    readonly_fields = ("criada_em",)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        corrida_id = getattr(obj, "id", None)

        class CorridaFormSet(formset):
            def _construct_form(self, i, **form_kwargs):
                form_kwargs["corrida_id"] = corrida_id
                return super()._construct_form(i, **form_kwargs)

        return CorridaFormSet


@admin.register(Corrida)
class CorridaAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "data",
        "local",
        "local_evento",
        "total_percursos",
        "total_inscricoes",
        "imagem",
    )
    list_filter = ("data",)
    search_fields = ("nome", "local", "local_evento")
    inlines = [PercursoCorridaInline, InscricaoInline]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(
            total_percursos_admin=Count("percursos", distinct=True),
            total_inscricoes_admin=Count("inscricoes", distinct=True),
        )

    def get_inline_instances(self, request, obj=None):
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

    def total_percursos(self, obj):
        return obj.total_percursos_admin

    total_percursos.short_description = "Distâncias"
    total_percursos.admin_order_field = "total_percursos_admin"

    def total_inscricoes(self, obj):
        return obj.total_inscricoes_admin

    total_inscricoes.short_description = "Inscrições"
    total_inscricoes.admin_order_field = "total_inscricoes_admin"


@admin.register(PercursoCorrida)
class PercursoCorridaAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "corrida",
        "distancia_km",
        "ativo",
        "ordem",
        "total_inscricoes",
        "tem_resultado",
    )
    list_filter = ("ativo", "corrida")
    search_fields = ("nome", "corrida__nome")
    ordering = ("corrida", "ordem", "distancia_km", "nome")
    inlines = [ResultadoPercursoInline]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("corrida").annotate(
            total_inscricoes_admin=Count("inscricoes", distinct=True),
        )

    def total_inscricoes(self, obj):
        return obj.total_inscricoes_admin

    total_inscricoes.short_description = "Inscrições"
    total_inscricoes.admin_order_field = "total_inscricoes_admin"

    def tem_resultado(self, obj):
        return "Sim" if hasattr(obj, "resultado") else "Nao"

    tem_resultado.short_description = "Resultado"


@admin.register(Participante)
class ParticipanteAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "idade",
        "equipe",
        "cpf",
        "cidade",
        "sexo",
        "tamanho_camisa",
        "categoria",
    )
    search_fields = ("nome", "equipe", "cidade", "categoria", "sexo", "cpf")
    list_filter = ("sexo", "categoria", "cidade")


@admin.register(Inscricao)
class InscricaoAdmin(admin.ModelAdmin):
    form = InscricaoAdminForm
    list_display = (
        "participante",
        "corrida",
        "percurso",
        "pago",
        "numero_chip",
        "criada_em",
    )
    autocomplete_fields = ("participante", "corrida")
    list_filter = ("pago", "corrida", "percurso", "criada_em")
    search_fields = (
        "participante__nome",
        "participante__cpf",
        "participante__cidade",
        "corrida__nome",
        "percurso__nome",
        "numero_chip",
    )
    ordering = ("-criada_em",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("participante", "corrida", "percurso")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        corrida_id = (
            request.POST.get("corrida")
            or request.GET.get("corrida")
            or getattr(obj, "corrida_id", None)
        )

        class RequestForm(form):
            def __init__(self, *args, **form_kwargs):
                form_kwargs["corrida_id"] = corrida_id
                super().__init__(*args, **form_kwargs)

        return RequestForm


@admin.register(ResultadoInscricao)
class ResultadoInscricaoAdmin(admin.ModelAdmin):
    list_display = (
        "inscricao",
        "tempo",
        "posicao_geral",
        "colocacao_categoria",
        "atualizado_em",
    )
    autocomplete_fields = ("inscricao",)
    list_filter = ("inscricao__corrida", "inscricao__percurso", "criado_em")
    search_fields = (
        "inscricao__participante__nome",
        "inscricao__participante__cpf",
        "inscricao__numero_chip",
        "inscricao__corrida__nome",
        "inscricao__percurso__nome",
    )
    ordering = ("posicao_geral",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "inscricao__participante",
            "inscricao__corrida",
            "inscricao__percurso",
        )


@admin.register(ArquivoExcel)
class ArquivoExcelAdmin(admin.ModelAdmin):
    form = ArquivoExcelAdminForm
    list_display = (
        "nome",
        "corrida_resultado",
        "percurso",
        "data_corrida",
        "criado_em",
        "preview_imagem",
    )
    list_filter = ("corrida", "percurso", "criado_em")
    search_fields = ("nome", "corrida__nome", "percurso__nome", "percurso__corrida__nome")
    autocomplete_fields = ("corrida", "percurso")
    fields = ("corrida", "percurso", "nome", "data_corrida", "local", "arquivo", "imagem")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        corrida_id = (
            request.POST.get("corrida")
            or request.GET.get("corrida")
            or getattr(obj, "corrida_id", None)
            or getattr(getattr(obj, "percurso", None), "corrida_id", None)
        )

        class RequestForm(form):
            def __init__(self, *args, **form_kwargs):
                form_kwargs["corrida_id"] = corrida_id
                super().__init__(*args, **form_kwargs)

        return RequestForm

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("corrida", "percurso__corrida")

    def corrida_resultado(self, obj):
        if obj.corrida_id:
            return obj.corrida
        if obj.percurso_id:
            return obj.percurso.corrida
        return "-"

    corrida_resultado.short_description = "Evento"

    def preview_imagem(self, obj):
        if obj.imagem:
            return format_html(
                '<img src="{}" width="120" style="border-radius:6px;" />',
                obj.imagem.url,
            )
        return "-"

    preview_imagem.short_description = "Imagem"


@admin.register(Corredor)
class CorredorAdmin(admin.ModelAdmin):
    list_display = (
        "colocacao",
        "numero",
        "nome",
        "distancia",
        "categoria",
        "arquivo",
    )
    search_fields = ("nome", "numero")
    list_filter = ("arquivo__corrida", "arquivo__percurso__corrida", "distancia", "arquivo", "categoria")
    ordering = ("colocacao",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("arquivo__percurso__corrida", "participante")
