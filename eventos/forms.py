from django import forms

from .models import ArquivoExcel, Corrida, Inscricao, Participante, PercursoCorrida


class ParticipanteForm(forms.ModelForm):
    class Meta:
        model = Participante
        fields = ['nome', 'data_nascimento', 'cidade', 'equipe', 'cpf', 'sexo', 'tamanho_camisa']
        labels = {
            'cpf': 'CPF',
        }


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
