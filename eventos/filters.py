import re
import unicodedata

import django_filters
from django.db.models import Q

from eventos.models import Corredor


def _normalizar_texto(valor):
    texto = str(valor or "").strip()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(char for char in texto if not unicodedata.combining(char))


def categoria_sem_sexo(valor):
    categoria = _normalizar_texto(valor)
    categoria = re.sub(r"\b(masculino|feminino|masc\.?|fem\.?)\b", "", categoria, flags=re.I)
    categoria = re.sub(r"^\s*[MF]\s+(\d)", r"\1", categoria, flags=re.I)
    categoria = re.sub(r"\s*[-–—]\s*$", "", categoria)
    categoria = re.sub(r"\s{2,}", " ", categoria)
    return categoria.strip()


class CorredorFilter(django_filters.FilterSet):
    nome = django_filters.CharFilter(
        field_name="nome",
        lookup_expr="icontains",
        label="Nome",
    )
    categoria = django_filters.ChoiceFilter(
        method="filter_categoria",
        label="Categoria",
        empty_label="Todas",
    )
    sexo = django_filters.ChoiceFilter(
        choices=(("M", "Masculino"), ("F", "Feminino")),
        method="filter_sexo",
        label="Sexo",
        empty_label="Todos",
    )

    class Meta:
        model = Corredor
        fields = ["nome", "categoria", "sexo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categorias = []
        vistas = set()
        for categoria in self.queryset.values_list("categoria", flat=True).order_by("categoria").distinct():
            categoria_limpa = categoria_sem_sexo(categoria)
            if categoria_limpa and categoria_limpa not in vistas:
                categorias.append((categoria_limpa, categoria_limpa))
                vistas.add(categoria_limpa)
        self.filters["categoria"].extra["choices"] = categorias

    def filter_categoria(self, queryset, name, value):
        if not value:
            return queryset

        ids = [
            corredor_id
            for corredor_id, categoria in queryset.values_list("id", "categoria")
            if categoria_sem_sexo(categoria) == value
        ]
        return queryset.filter(id__in=ids)

    def filter_sexo(self, queryset, name, value):
        if value == "M":
            return queryset.filter(
                Q(participante__sexo="M")
                | Q(participante__isnull=True, categoria__icontains="masculino")
                | Q(participante__isnull=True, categoria__startswith="M")
            )
        if value == "F":
            return queryset.filter(
                Q(participante__sexo="F")
                | Q(participante__isnull=True, categoria__icontains="feminino")
                | Q(participante__isnull=True, categoria__startswith="F")
            )
        return queryset
