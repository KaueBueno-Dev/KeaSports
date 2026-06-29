from rest_framework import serializers

from eventos.models import Corrida, Inscricao, PercursoCorrida, ResultadoInscricao


class EventoCronometragemSerializer(serializers.ModelSerializer):
    class Meta:
        model = Corrida
        fields = ("id", "nome", "data", "local")


class PercursoCronometragemSerializer(serializers.ModelSerializer):
    evento_id = serializers.IntegerField(source="corrida_id", read_only=True)

    class Meta:
        model = PercursoCorrida
        fields = ("id", "evento_id", "nome", "distancia_km", "ordem")


class InscricaoPagaCronometragemSerializer(serializers.ModelSerializer):
    evento_id = serializers.IntegerField(source="corrida_id", read_only=True)
    percurso_id = serializers.IntegerField(read_only=True)
    atleta_id = serializers.IntegerField(source="participante_id", read_only=True)
    nome_atleta = serializers.CharField(source="participante.nome", read_only=True)
    sexo = serializers.CharField(source="participante.sexo", read_only=True)
    categoria = serializers.CharField(source="participante.categoria", read_only=True)
    cidade = serializers.CharField(source="participante.cidade", read_only=True)
    equipe = serializers.CharField(source="participante.equipe", read_only=True)

    class Meta:
        model = Inscricao
        fields = (
            "id",
            "evento_id",
            "percurso_id",
            "atleta_id",
            "nome_atleta",
            "sexo",
            "categoria",
            "cidade",
            "equipe",
            "numero_chip",
        )


class AtualizarChipSerializer(serializers.Serializer):
    numero_chip = serializers.CharField(max_length=30, allow_blank=False, trim_whitespace=True)


class ResultadoCronometragemSerializer(serializers.Serializer):
    inscricao_id = serializers.IntegerField()
    tempo = serializers.CharField(max_length=30, allow_blank=False, trim_whitespace=True)
    posicao_geral = serializers.IntegerField(min_value=1)
    colocacao_categoria = serializers.IntegerField(min_value=1)


class ResultadoInscricaoSerializer(serializers.ModelSerializer):
    inscricao_id = serializers.IntegerField(source="inscricao.id", read_only=True)

    class Meta:
        model = ResultadoInscricao
        fields = (
            "id",
            "inscricao_id",
            "tempo",
            "posicao_geral",
            "colocacao_categoria",
            "criado_em",
            "atualizado_em",
        )
