from datetime import date

from django import forms

from .models import ArquivoExcel, Corrida, Inscricao, Participante, PercursoCorrida
from .services.categorias import (
    MENSAGEM_IDADE_MINIMA_INSCRICAO,
    calcular_categoria_por_data_nascimento,
    idade_permitida_para_inscricao,
)


class ParticipanteForm(forms.ModelForm):
    class Meta:
        model = Participante
        fields = [
            'nome',
            'data_nascimento',
            'cidade',
            'equipe',
            'cpf',
            'sexo',
            'tamanho_camisa',
        ]
        labels = {
            'cpf': 'CPF',
        }

    def __init__(self, *args, categoria_referencia=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.categoria_referencia = categoria_referencia

    def clean(self):
        cleaned_data = super().clean()
        data_nascimento = cleaned_data.get("data_nascimento")
        referencia = self.categoria_referencia or date.today()
        if data_nascimento and (
            data_nascimento > date.today() or data_nascimento > referencia
        ):
            self.add_error("data_nascimento", "Data de nascimento nao pode ser futura.")
        elif data_nascimento and not idade_permitida_para_inscricao(
            data_nascimento,
            referencia=referencia,
        ):
            self.add_error("data_nascimento", MENSAGEM_IDADE_MINIMA_INSCRICAO)

        cleaned_data["_categoria_calculada"] = calcular_categoria_por_data_nascimento(
            data_nascimento,
            referencia=referencia,
        )
        return cleaned_data


class PercursoPorCorridaMixin:
    percurso_empty_label = "Selecione primeiro a corrida"

    def filtrar_percurso_por_corrida(self, corrida_id):
        field = self.fields.get("percurso")
        if not field:
            return

        if corrida_id:
            field.queryset = PercursoCorrida.objects.filter(
                corrida_id=corrida_id,
            ).select_related("corrida")
        else:
            field.queryset = PercursoCorrida.objects.none()
            field.empty_label = self.percurso_empty_label


class InscricaoAdminForm(PercursoPorCorridaMixin, forms.ModelForm):
    cidade = forms.CharField(label="Cidade", max_length=100, required=False)

    class Meta:
        model = Inscricao
        fields = "__all__"

    class Media:
        js = ("eventos/admin_percurso_filter.js",)

    def __init__(self, *args, **kwargs):
        corrida_id = kwargs.pop("corrida_id", None)
        super().__init__(*args, **kwargs)
        corrida_id = (
            corrida_id
            or self.data.get("corrida")
            or self.initial.get("corrida")
            or getattr(self.instance, "corrida_id", None)
        )
        self.filtrar_percurso_por_corrida(corrida_id)
        self._preencher_cidade_inicial()

    def _participante_selecionado(self):
        participante_id = (
            self.data.get("participante")
            if self.is_bound
            else self.initial.get("participante")
        )
        participante = getattr(self.instance, "participante", None)
        if participante_id and str(getattr(participante, "pk", "")) != str(participante_id):
            return Participante.objects.filter(pk=participante_id).first()
        return participante

    def _preencher_cidade_inicial(self):
        if self.is_bound:
            return
        participante = self._participante_selecionado()
        if participante:
            cidade = participante.cidade or ""
            self.fields["cidade"].initial = cidade
            self.initial["cidade"] = cidade

    def clean(self):
        cleaned_data = super().clean()
        corrida = cleaned_data.get("corrida")
        percurso = cleaned_data.get("percurso")
        if corrida and percurso and percurso.corrida_id != corrida.id:
            self.add_error("percurso", "Percurso invalido para esta corrida.")
        return cleaned_data


class ArquivoExcelAdminForm(PercursoPorCorridaMixin, forms.ModelForm):
    class Meta:
        model = ArquivoExcel
        fields = "__all__"

    class Media:
        js = ("eventos/admin_percurso_filter.js",)

    def __init__(self, *args, **kwargs):
        corrida_id = kwargs.pop("corrida_id", None)
        super().__init__(*args, **kwargs)
        self.fields["corrida"].queryset = Corrida.objects.order_by("nome", "data")
        self.fields["percurso"].required = False

        percurso = getattr(self.instance, "percurso", None)
        corrida_id = (
            corrida_id
            or self.data.get("corrida")
            or self.initial.get("corrida")
            or getattr(self.instance, "corrida_id", None)
            or getattr(percurso, "corrida_id", None)
        )
        if corrida_id:
            self.filtrar_percurso_por_corrida(corrida_id)
        else:
            self.fields["percurso"].queryset = (
                PercursoCorrida.objects.select_related("corrida")
                .order_by("corrida__nome", "ordem", "distancia_km", "nome")
            )

    def clean(self):
        cleaned_data = super().clean()
        corrida = cleaned_data.get("corrida")
        percurso = cleaned_data.get("percurso")

        if not corrida and not percurso:
            self.add_error("corrida", "Informe a corrida ou o percurso do resultado.")
        if percurso:
            if corrida and percurso.corrida_id != corrida.id:
                self.add_error("percurso", "Percurso invalido para esta corrida.")
            cleaned_data["corrida"] = percurso.corrida
        return cleaned_data
