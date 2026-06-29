from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from eventos.api_authentication import CronometragemAPIKeyAuthentication
from eventos.api_serializers import (
    AtualizarChipSerializer,
    EventoCronometragemSerializer,
    InscricaoPagaCronometragemSerializer,
    PercursoCronometragemSerializer,
    ResultadoCronometragemSerializer,
    ResultadoInscricaoSerializer,
)
from eventos.models import Corrida, Inscricao, PercursoCorrida, ResultadoInscricao


class CronometragemAPIView(APIView):
    authentication_classes = [CronometragemAPIKeyAuthentication]
    permission_classes = [IsAuthenticated]


class EventosCronometragemView(CronometragemAPIView):
    def get(self, request):
        eventos = Corrida.objects.all().order_by("-data", "nome")
        serializer = EventoCronometragemSerializer(eventos, many=True)
        return Response(serializer.data)


class PercursosCronometragemView(CronometragemAPIView):
    def get(self, request, evento_id):
        if not Corrida.objects.filter(pk=evento_id).exists():
            return Response(
                {"detail": "Evento não encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )

        percursos = (
            PercursoCorrida.objects
            .filter(corrida_id=evento_id, ativo=True)
            .order_by("ordem", "distancia_km", "nome")
        )
        serializer = PercursoCronometragemSerializer(percursos, many=True)
        return Response(serializer.data)


class InscricoesPagasCronometragemView(CronometragemAPIView):
    def get(self, request):
        evento_id = request.query_params.get("evento_id")
        percurso_id = request.query_params.get("percurso_id")

        if not evento_id or not percurso_id:
            return Response(
                {"detail": "Informe evento_id e percurso_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inscricoes = (
            Inscricao.objects
            .select_related("participante", "corrida", "percurso")
            .filter(
                corrida_id=evento_id,
                percurso_id=percurso_id,
                pago=True,
            )
            .order_by("participante__nome", "id")
        )
        serializer = InscricaoPagaCronometragemSerializer(inscricoes, many=True)
        return Response(serializer.data)


class AtualizarChipInscricaoView(CronometragemAPIView):
    def patch(self, request, inscricao_id):
        serializer = AtualizarChipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            inscricao = Inscricao.objects.get(pk=inscricao_id)
        except Inscricao.DoesNotExist:
            return Response(
                {"detail": "Inscrição não encontrada."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not inscricao.pago:
            return Response(
                {"detail": "Inscrição não está paga."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inscricao.numero_chip = serializer.validated_data["numero_chip"]
        inscricao.save(update_fields=["numero_chip", "atualizada_em"])
        return Response(
            {
                "id": inscricao.id,
                "numero_chip": inscricao.numero_chip,
            }
        )


class EnviarResultadoCronometragemView(CronometragemAPIView):
    def post(self, request):
        serializer = ResultadoCronometragemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            inscricao = Inscricao.objects.get(pk=data["inscricao_id"])
        except Inscricao.DoesNotExist:
            return Response(
                {"detail": "Inscrição não encontrada."},
                status=status.HTTP_404_NOT_FOUND,
            )

        resultado, _ = ResultadoInscricao.objects.update_or_create(
            inscricao=inscricao,
            defaults={
                "tempo": data["tempo"],
                "posicao_geral": data["posicao_geral"],
                "colocacao_categoria": data["colocacao_categoria"],
            },
        )
        return Response(ResultadoInscricaoSerializer(resultado).data)
