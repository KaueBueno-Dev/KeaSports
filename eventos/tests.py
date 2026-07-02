from datetime import date, timedelta
from io import BytesIO
from unittest.mock import Mock, patch

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.db.models.signals import post_save
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from requests import exceptions as requests_exceptions

from eventos.forms import ArquivoExcelAdminForm, InscricaoAdminForm
from eventos.admin import ADMIN_MODEL_ORDER, ArquivoExcelAdmin, CorridaAdmin, InscricaoAdmin
from eventos.filters import CorredorFilter, categoria_sem_sexo
from eventos.models import ArquivoExcel, Corredor, Corrida, Inscricao, Participante, PercursoCorrida, ResultadoInscricao
from eventos.services.categorias import calcular_categoria_por_data_nascimento
from eventos.signals import extrair_dados_excel, normalizar_distancia
from eventos.views.corrida_views import calcular_rankinkg


CPF_VALIDO = "52998224725"
CPF_VALIDO_2 = "39053344705"
EXCEL_TEST_FILE = SimpleUploadedFile(
    "resultado.xlsx",
    b"conteudo-de-teste",
    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


def excel_upload(nome, linhas):
    import pandas as pd

    buffer = BytesIO()
    pd.DataFrame(linhas).to_excel(buffer, index=False)
    buffer.seek(0)
    return SimpleUploadedFile(
        nome,
        buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@override_settings(STORAGES=TEST_STORAGES)
class CadastroParticipanteTests(TestCase):
    def test_cadastro_cria_participante_sem_corrida(self):
        response = self.client.post(
            reverse("register"),
            {
                "nome_completo": "Maria Corredora",
                "cpf": CPF_VALIDO,
                "password": "SenhaForte123!",
                "confirmar_senha": "SenhaForte123!",
                "data_nascimento": "1990-05-10",
                "sexo": "F",
                "tamanho_camisa": "M",
                "cidade": "Sorocaba",
                "equipe": "Kea Team",
            },
        )

        self.assertRedirects(response, reverse("cadastro"))
        self.assertTrue(User.objects.filter(username=CPF_VALIDO).exists())

        participante = Participante.objects.get(cpf=CPF_VALIDO)
        self.assertEqual(participante.nome, "Maria Corredora")
        self.assertEqual(participante.usuario.username, CPF_VALIDO)
        self.assertEqual(participante.cidade, "Sorocaba")
        self.assertFalse(Inscricao.objects.filter(participante=participante).exists())


@override_settings(STORAGES=TEST_STORAGES)
class InscricaoCorridaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username=CPF_VALIDO,
            password="SenhaForte123!",
            first_name="Joao Corredor",
        )
        self.participante = Participante.objects.create(
            usuario=self.user,
            nome="Joao Corredor",
            cpf=CPF_VALIDO,
            data_nascimento=date(1991, 4, 20),
            sexo="M",
            tamanho_camisa="G",
            cidade="Sorocaba",
            equipe="Equipe A",
        )
        self.corrida_1 = Corrida.objects.create(
            nome="Corrida Centro",
            local="Centro",
            data=date.today() + timedelta(days=10),
        )
        self.percurso_5k = PercursoCorrida.objects.create(
            corrida=self.corrida_1,
            nome="5 km",
            distancia_km=5,
        )
        self.corrida_2 = Corrida.objects.create(
            nome="Corrida Praia",
            local="Praia",
            data=date.today() + timedelta(days=30),
        )
        self.percurso_10k = PercursoCorrida.objects.create(
            corrida=self.corrida_2,
            nome="10 km",
            distancia_km=10,
        )
        self._cpf_indice = 10000000000
        self.client.login(username=CPF_VALIDO, password="SenhaForte123!")

    def payload_inscricao(self, **overrides):
        payload = {
            "cpf": CPF_VALIDO,
            "nome": "Joao Corredor",
            "data_nascimento": "1991-04-20",
            "sexo": "M",
            "tamanho_camisa": "G",
            "cidade": "Sorocaba",
            "equipe": "Equipe A",
        }
        payload.update(overrides)
        return payload

    def criar_inscricao_extra(self, corrida=None, percurso=None, pago=True):
        self._cpf_indice += 1
        participante = Participante.objects.create(
            nome=f"Atleta {self._cpf_indice}",
            cpf=str(self._cpf_indice),
            data_nascimento=date(1990, 1, 1),
            sexo="M",
            tamanho_camisa="M",
        )
        corrida = corrida or self.corrida_1
        percurso = percurso or self.percurso_5k
        return Inscricao.objects.create(
            participante=participante,
            corrida=corrida,
            percurso=percurso,
            pago=pago,
        )

    def test_inscricao_com_percurso_unico_seleciona_automaticamente(self):
        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            {
                "cpf": CPF_VALIDO,
                "nome": "Joao Corredor Atualizado",
                "data_nascimento": "1991-04-20",
                "sexo": "M",
                "tamanho_camisa": "M",
                "cidade": "Votorantim",
                "equipe": "Equipe B",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Participante.objects.filter(cpf=CPF_VALIDO).count(), 1)
        self.assertTrue(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

        self.participante.refresh_from_db()
        self.assertEqual(self.participante.nome, "Joao Corredor Atualizado")
        self.assertEqual(self.participante.cidade, "Votorantim")
        self.assertEqual(self.participante.equipe, "Equipe B")
        inscricao = Inscricao.objects.get(
            participante=self.participante,
            corrida=self.corrida_1,
        )
        self.assertEqual(inscricao.percurso, self.percurso_5k)
        self.assertFalse(inscricao.pago)

    def test_evento_sem_limite_permite_inscricao(self):
        self.assertIsNone(self.corrida_1.limite_inscritos)

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            self.payload_inscricao(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

    def test_evento_com_limite_maior_que_pagas_permite_inscricao(self):
        self.corrida_1.limite_inscritos = 2
        self.corrida_1.save(update_fields=["limite_inscritos"])
        self.criar_inscricao_extra(pago=True)

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            self.payload_inscricao(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.corrida_1.vagas_ocupadas, 1)
        self.assertTrue(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

    def test_evento_com_limite_igual_a_pagas_bloqueia_nova_inscricao(self):
        self.corrida_1.limite_inscritos = 1
        self.corrida_1.save(update_fields=["limite_inscritos"])
        self.criar_inscricao_extra(pago=True)

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            self.payload_inscricao(),
        )

        self.assertContains(response, "Inscrições esgotadas")
        self.assertFalse(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

    def test_inscricoes_nao_pagas_nao_ocupam_vaga(self):
        self.corrida_1.limite_inscritos = 1
        self.corrida_1.save(update_fields=["limite_inscritos"])
        self.criar_inscricao_extra(pago=False)

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            self.payload_inscricao(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.corrida_1.vagas_ocupadas, 0)
        self.assertTrue(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

    def test_evento_esgotado_exibe_mensagem(self):
        self.corrida_1.limite_inscritos = 1
        self.corrida_1.save(update_fields=["limite_inscritos"])
        self.criar_inscricao_extra(pago=True)

        response = self.client.get(reverse("inscrever_corrida", args=[self.corrida_1.id]))

        self.assertContains(response, "Inscrições esgotadas")
        self.assertNotContains(response, "Buscar dados")

    def test_aumentar_limite_permite_nova_inscricao(self):
        self.corrida_1.limite_inscritos = 1
        self.corrida_1.save(update_fields=["limite_inscritos"])
        self.criar_inscricao_extra(pago=True)

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            self.payload_inscricao(),
        )
        self.assertContains(response, "Inscrições esgotadas")

        self.corrida_1.limite_inscritos = 2
        self.corrida_1.save(update_fields=["limite_inscritos"])
        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            self.payload_inscricao(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

    def test_post_direto_em_evento_esgotado_e_bloqueado(self):
        self.corrida_1.limite_inscritos = 1
        self.corrida_1.save(update_fields=["limite_inscritos"])
        self.criar_inscricao_extra(pago=True)

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            self.payload_inscricao(),
        )

        self.assertContains(response, "Inscrições esgotadas")
        self.assertFalse(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

    def test_inscricao_existente_em_evento_esgotado_nao_duplica_nem_quebra(self):
        Inscricao.objects.create(
            participante=self.participante,
            corrida=self.corrida_1,
            percurso=self.percurso_5k,
            pago=False,
        )
        self.corrida_1.limite_inscritos = 1
        self.corrida_1.save(update_fields=["limite_inscritos"])
        self.criar_inscricao_extra(pago=True)

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            self.payload_inscricao(cidade="Votorantim"),
        )

        self.assertContains(response, "Você já estava inscrito nesta corrida.")
        self.assertEqual(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).count(),
            1,
        )
        self.participante.refresh_from_db()
        self.assertEqual(self.participante.cidade, "Votorantim")

    def test_categoria_e_calculada_pela_data_de_nascimento(self):
        casos = [
            (date(2013, 6, 30), ""),
            (date(2012, 6, 30), "Menor de 18"),
            (date(2010, 6, 30), "Menor de 18"),
            (date(2008, 6, 30), "18-24"),
            (date(2001, 6, 30), "25-29"),
            (date(1996, 6, 30), "30-34"),
            (date(1989, 11, 7), "35-39"),
            (date(1986, 6, 30), "40-44"),
            (date(1961, 6, 30), "65-69"),
            (date(1950, 6, 30), "65+"),
        ]

        for data_nascimento, categoria_esperada in casos:
            with self.subTest(data_nascimento=data_nascimento):
                categoria = calcular_categoria_por_data_nascimento(
                    data_nascimento,
                    referencia=date(2026, 6, 30),
                )
                self.assertEqual(categoria, categoria_esperada)

    def test_nascimento_1989_11_07_fica_em_35_39(self):
        categoria = calcular_categoria_por_data_nascimento(
            date(1989, 11, 7),
            referencia=date(2026, 6, 30),
        )

        self.assertEqual(categoria, "35-39")

    def test_participante_com_13_anos_nao_consegue_se_inscrever(self):
        self.corrida_1.data = date(2026, 6, 30)
        self.corrida_1.save(update_fields=["data"])

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            {
                "cpf": CPF_VALIDO,
                "nome": "Joao Corredor",
                "data_nascimento": "2013-06-30",
                "sexo": "M",
                "tamanho_camisa": "M",
                "cidade": "Sorocaba",
                "equipe": "Equipe A",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Nao e permitido realizar inscricao para participantes menores de 14 anos.",
        )
        self.assertFalse(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

    def test_participante_que_ainda_nao_completou_14_nao_consegue_se_inscrever(self):
        self.corrida_1.data = date(2026, 6, 30)
        self.corrida_1.save(update_fields=["data"])

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            {
                "cpf": CPF_VALIDO,
                "nome": "Joao Corredor",
                "data_nascimento": "2012-07-01",
                "sexo": "M",
                "tamanho_camisa": "M",
                "cidade": "Sorocaba",
                "equipe": "Equipe A",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Nao e permitido realizar inscricao para participantes menores de 14 anos.",
        )
        self.assertFalse(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

    def test_participante_com_14_anos_completos_consegue_se_inscrever(self):
        self.corrida_1.data = date(2026, 6, 30)
        self.corrida_1.save(update_fields=["data"])

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            {
                "cpf": CPF_VALIDO,
                "nome": "Joao Corredor",
                "data_nascimento": "2012-06-30",
                "sexo": "M",
                "tamanho_camisa": "M",
                "cidade": "Sorocaba",
                "equipe": "Equipe A",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.participante.refresh_from_db()
        self.assertEqual(self.participante.categoria, "Menor de 18")
        self.assertTrue(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

    def test_post_malicioso_nao_burla_idade_minima(self):
        self.corrida_1.data = date(2026, 6, 30)
        self.corrida_1.save(update_fields=["data"])

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            {
                "cpf": CPF_VALIDO,
                "nome": "Joao Corredor",
                "data_nascimento": "2013-06-30",
                "categoria": "35-39",
                "sexo": "M",
                "tamanho_camisa": "M",
                "cidade": "Sorocaba",
                "equipe": "Equipe A",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Nao e permitido realizar inscricao para participantes menores de 14 anos.",
        )
        self.assertFalse(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).exists()
        )

    def test_model_inscricao_bloqueia_participante_menor_de_14_anos(self):
        self.corrida_1.data = date(2026, 6, 30)
        self.corrida_1.save(update_fields=["data"])
        usuario_menor = User.objects.create_user(
            username=CPF_VALIDO_2,
            password="SenhaForte123!",
        )
        participante_menor = Participante.objects.create(
            usuario=usuario_menor,
            nome="Atleta Menor",
            cpf=CPF_VALIDO_2,
            data_nascimento=date(2013, 6, 30),
            sexo="M",
            tamanho_camisa="M",
            cidade="Sorocaba",
        )

        with self.assertRaises(ValidationError):
            Inscricao.objects.create(
                participante=participante_menor,
                corrida=self.corrida_1,
                percurso=self.percurso_5k,
            )

    def test_inscricao_ignora_categoria_manual_enviada_pelo_usuario(self):
        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            {
                "cpf": CPF_VALIDO,
                "nome": "Joao Corredor",
                "data_nascimento": "1991-04-20",
                "categoria": "65+",
                "sexo": "M",
                "tamanho_camisa": "M",
                "cidade": "Sorocaba",
                "equipe": "Equipe A",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.participante.refresh_from_db()
        self.assertEqual(self.participante.categoria, "35-39")

    def test_alteracao_da_data_de_nascimento_atualiza_categoria(self):
        hoje = date.today()
        nova_data = date(hoje.year - 22, hoje.month, hoje.day)

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            {
                "cpf": CPF_VALIDO,
                "nome": "Joao Corredor",
                "data_nascimento": nova_data.isoformat(),
                "sexo": "M",
                "tamanho_camisa": "M",
                "cidade": "Sorocaba",
                "equipe": "Equipe A",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.participante.refresh_from_db()
        self.assertEqual(self.participante.data_nascimento, nova_data)
        self.assertEqual(self.participante.categoria, "18-24")

    def test_formulario_de_inscricao_exibe_categoria_somente_visual(self):
        response = self.client.get(reverse("inscrever_corrida", args=[self.corrida_1.id]))

        self.assertContains(response, 'id="categoria"')
        self.assertContains(response, 'id="categoria-referencia-data"')
        self.assertContains(response, "Informe a data de nascimento")
        self.assertNotContains(response, 'name="categoria"')
        self.assertContains(response, "Para alterar a categoria, ajuste a data de nascimento.")

    def test_busca_cpf_retorna_categoria_calculada_quando_campo_esta_vazio(self):
        Participante.objects.filter(pk=self.participante.pk).update(categoria="")
        self.participante.refresh_from_db()

        response = self.client.get(
            reverse("buscar_usuario_cpf"),
            {"cpf": CPF_VALIDO},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["categoria"], "35-39")

    def test_mesmo_participante_pode_se_inscrever_em_multiplas_corridas(self):
        for corrida in (self.corrida_1, self.corrida_2):
            self.client.post(
                reverse("inscrever_corrida", args=[corrida.id]),
                {
                    "cpf": CPF_VALIDO,
                    "nome": "Joao Corredor",
                    "data_nascimento": "1991-04-20",
                    "sexo": "M",
                    "tamanho_camisa": "G",
                    "cidade": "Sorocaba",
                    "equipe": "Equipe A",
                },
            )

        self.assertEqual(
            Inscricao.objects.filter(participante=self.participante).count(),
            2,
        )
        self.assertTrue(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_2,
                percurso=self.percurso_10k,
            ).exists()
        )

    def test_inscricao_com_multiplos_percursos_exige_escolha(self):
        PercursoCorrida.objects.create(
            corrida=self.corrida_1,
            nome="10 km",
            distancia_km=10,
            ordem=1,
        )

        payload = {
            "cpf": CPF_VALIDO,
            "nome": "Joao Corredor",
            "data_nascimento": "1991-04-20",
            "sexo": "M",
            "tamanho_camisa": "G",
            "cidade": "Sorocaba",
            "equipe": "Equipe A",
        }

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            payload,
        )

        self.assertContains(response, "Escolha o percurso/distancia da sua inscricao.")
        self.assertFalse(Inscricao.objects.filter(corrida=self.corrida_1).exists())

        payload["percurso"] = str(self.percurso_5k.id)
        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            payload,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
                percurso=self.percurso_5k,
            ).exists()
        )

    def test_inscricao_rejeita_percurso_de_outra_corrida(self):
        PercursoCorrida.objects.create(
            corrida=self.corrida_1,
            nome="10 km",
            distancia_km=10,
            ordem=1,
        )

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            {
                "cpf": CPF_VALIDO,
                "nome": "Joao Corredor",
                "data_nascimento": "1991-04-20",
                "sexo": "M",
                "tamanho_camisa": "G",
                "cidade": "Sorocaba",
                "equipe": "Equipe A",
                "percurso": str(self.percurso_10k.id),
            },
        )

        self.assertContains(response, "Percurso invalido para esta corrida.")
        self.assertFalse(Inscricao.objects.filter(corrida=self.corrida_1).exists())

    def test_model_inscricao_rejeita_percurso_de_outra_corrida(self):
        inscricao = Inscricao(
            participante=self.participante,
            corrida=self.corrida_1,
            percurso=self.percurso_10k,
        )

        with self.assertRaisesMessage(ValidationError, "Percurso invalido para esta corrida."):
            inscricao.full_clean()

    def test_admin_form_filtra_percurso_pela_corrida(self):
        form = InscricaoAdminForm(corrida_id=self.corrida_1.id)

        self.assertIn(self.percurso_5k, form.fields["percurso"].queryset)
        self.assertNotIn(self.percurso_10k, form.fields["percurso"].queryset)

    def test_inscricao_repetida_na_mesma_corrida_nao_duplica(self):
        payload = {
            "cpf": CPF_VALIDO,
            "nome": "Joao Corredor",
            "data_nascimento": "1991-04-20",
            "sexo": "M",
            "tamanho_camisa": "G",
            "cidade": "Sorocaba",
            "equipe": "Equipe A",
        }

        self.client.post(reverse("inscrever_corrida", args=[self.corrida_1.id]), payload)
        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            payload,
        )

        self.assertContains(response, "Você já estava inscrito nesta corrida.")
        self.assertEqual(
            Inscricao.objects.filter(
                participante=self.participante,
                corrida=self.corrida_1,
            ).count(),
            1,
        )

    def test_usuario_nao_pode_inscrever_cpf_de_outra_pessoa(self):
        Participante.objects.create(
            nome="Outra Pessoa",
            cpf=CPF_VALIDO_2,
            data_nascimento=date(1995, 1, 1),
            sexo="F",
            tamanho_camisa="P",
        )

        response = self.client.post(
            reverse("inscrever_corrida", args=[self.corrida_1.id]),
            {
                "cpf": CPF_VALIDO_2,
                "nome": "Outra Pessoa",
                "data_nascimento": "1995-01-01",
                "sexo": "F",
                "tamanho_camisa": "P",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Inscricao.objects.filter(corrida=self.corrida_1).exists())


@override_settings(STORAGES=TEST_STORAGES)
class CorridaAdminFluxoTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="SenhaForte123!",
        )
        self.model_admin = CorridaAdmin(Corrida, admin.site)

    def _request(self):
        request = self.factory.get("/admin/api_s/corrida/add/")
        request.user = self.admin_user
        return request

    def test_criacao_de_corrida_exibe_apenas_inline_de_percursos(self):
        inlines = self.model_admin.get_inline_instances(self._request(), obj=None)
        inline_models = {inline.model for inline in inlines}

        self.assertIn(PercursoCorrida, inline_models)
        self.assertNotIn(Inscricao, inline_models)

    def test_edicao_de_corrida_exibe_inlines_de_percurso_e_inscricao(self):
        corrida = Corrida.objects.create(
            nome="Corrida Admin",
            local="Sorocaba",
            data=date.today(),
        )

        inlines = self.model_admin.get_inline_instances(self._request(), obj=corrida)
        inline_models = {inline.model for inline in inlines}

        self.assertIn(PercursoCorrida, inline_models)
        self.assertIn(Inscricao, inline_models)

    def test_corrida_tem_nome_administrativo_correto(self):
        self.assertEqual(Corrida._meta.verbose_name, "Evento")
        self.assertEqual(Corrida._meta.verbose_name_plural, "Eventos")
        self.assertEqual(PercursoCorrida._meta.verbose_name, "Distância")
        self.assertEqual(PercursoCorrida._meta.verbose_name_plural, "Distâncias")
        self.assertEqual(Participante._meta.verbose_name, "Atleta")
        self.assertEqual(Participante._meta.verbose_name_plural, "Atletas")
        self.assertEqual(Inscricao._meta.verbose_name, "Inscrição")
        self.assertEqual(Inscricao._meta.verbose_name_plural, "Inscrições")
        self.assertEqual(ArquivoExcel._meta.verbose_name, "Resultado")
        self.assertEqual(ArquivoExcel._meta.verbose_name_plural, "Resultados")
        self.assertEqual(ResultadoInscricao._meta.verbose_name, "Resultado da Cronometragem")
        self.assertEqual(ResultadoInscricao._meta.verbose_name_plural, "Resultados da Cronometragem")
        self.assertEqual(Corredor._meta.verbose_name, "Classificação")
        self.assertEqual(Corredor._meta.verbose_name_plural, "Classificações")

    def test_admin_menu_usa_ordem_operacional(self):
        app_list = admin.site.get_app_list(self._request())
        api_s_app = next(app for app in app_list if app["app_label"] == "api_s")
        model_names = [
            model["object_name"]
            for model in api_s_app["models"]
            if model["object_name"] in ADMIN_MODEL_ORDER
        ]

        self.assertEqual(
            model_names,
            [
                "Corrida",
                "Participante",
                "Inscricao",
                "ArquivoExcel",
                "ResultadoInscricao",
                "Corredor",
            ],
        )
        self.assertIn(PercursoCorrida, admin.site._registry)
        self.assertNotIn("PercursoCorrida", model_names)

    def test_inline_percurso_bloqueia_exclusao_quando_tem_inscricao(self):
        corrida = Corrida.objects.create(
            nome="Corrida Admin",
            local="Sorocaba",
            data=date.today(),
        )
        percurso = PercursoCorrida.objects.create(
            corrida=corrida,
            nome="5 km",
            distancia_km=5,
        )
        participante = Participante.objects.create(
            nome="Atleta Inline",
            cpf="12312312312",
            data_nascimento=date(1990, 1, 1),
            sexo="M",
            tamanho_camisa="M",
        )
        Inscricao.objects.create(
            participante=participante,
            corrida=corrida,
            percurso=percurso,
        )
        inline = next(
            inline
            for inline in self.model_admin.get_inline_instances(self._request(), obj=corrida)
            if inline.model is PercursoCorrida
        )
        formset_class = inline.get_formset(self._request(), obj=corrida)
        prefix = formset_class(instance=corrida).prefix
        data = {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "1",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
            f"{prefix}-0-id": str(percurso.pk),
            f"{prefix}-0-corrida": str(corrida.pk),
            f"{prefix}-0-nome": percurso.nome,
            f"{prefix}-0-distancia_km": "5",
            f"{prefix}-0-ativo": "on",
            f"{prefix}-0-ordem": "0",
            f"{prefix}-0-DELETE": "on",
        }

        formset = formset_class(data=data, instance=corrida, prefix=prefix)

        self.assertFalse(formset.is_valid())
        self.assertIn("Nao e possivel remover", str(formset.non_form_errors()))

    def test_corrida_admin_tem_atalhos_operacionais_por_evento(self):
        corrida = Corrida.objects.create(
            nome="Corrida Admin",
            local="Sorocaba",
            data=date.today(),
        )

        inscricoes_link = str(self.model_admin.inscricoes_evento(corrida))
        chips_link = str(self.model_admin.chips_evento(corrida))
        inscricoes_url = reverse("admin:api_s_inscricao_changelist")

        self.assertIn(inscricoes_url, inscricoes_link)
        self.assertIn(f"corrida__id__exact={corrida.id}", inscricoes_link)
        self.assertIn(inscricoes_url, chips_link)
        self.assertIn(f"corrida__id__exact={corrida.id}", chips_link)
        self.assertIn("pago__exact=1", chips_link)

    def test_inscricao_admin_permite_atribuicao_de_chip_na_lista(self):
        model_admin = InscricaoAdmin(Inscricao, admin.site)

        self.assertIn("numero_chip", model_admin.list_display)
        self.assertIn("numero_chip", model_admin.list_editable)
        self.assertIn("corrida", model_admin.list_filter)
        self.assertIn("cidade_participante", model_admin.list_display)
        self.assertIn("cidade", model_admin.fields)
        self.assertNotIn("cidade", model_admin.readonly_fields)

        participante = Participante.objects.create(
            nome="Atleta Cidade",
            cpf="12345678901",
            data_nascimento=date(1990, 1, 1),
            sexo="M",
            tamanho_camisa="M",
            cidade="Itarare",
        )
        corrida = Corrida.objects.create(
            nome="Corrida Cidade",
            local="Itarare",
            data=date.today(),
        )
        percurso = PercursoCorrida.objects.create(
            corrida=corrida,
            nome="5 km",
            distancia_km=5,
        )
        inscricao = Inscricao.objects.create(
            participante=participante,
            corrida=corrida,
            percurso=percurso,
        )

        self.assertEqual(model_admin.cidade_participante(inscricao), "Itarare")
        self.assertEqual(model_admin.cidade_participante(None), "-")

        form = InscricaoAdminForm(instance=inscricao)
        self.assertIn("cidade", form.fields)
        self.assertEqual(form.initial["cidade"], "Itarare")

        participante.cidade = ""
        participante.save(update_fields=["cidade"])
        form = InscricaoAdminForm(
            data={
                "participante": str(participante.pk),
                "cidade": "Itarare",
                "corrida": str(corrida.pk),
                "percurso": str(percurso.pk),
                "pago": "",
                "numero_chip": "",
            },
            instance=inscricao,
            corrida_id=corrida.pk,
        )
        self.assertTrue(form.is_valid(), form.errors)

        model_admin.save_model(self._request(), form.save(commit=False), form, change=True)

        participante.refresh_from_db()
        self.assertEqual(participante.cidade, "Itarare")


@override_settings(STORAGES=TEST_STORAGES)
class RankinkgTests(TestCase):
    def criar_arquivo_resultado(self):
        post_save.disconnect(extrair_dados_excel, sender=ArquivoExcel)
        try:
            corrida = Corrida.objects.create(
                nome="Etapa Teste",
                local="Sorocaba",
                data=date.today(),
            )
            percurso = PercursoCorrida.objects.create(
                corrida=corrida,
                nome="5 km",
                distancia_km=5,
            )
            return ArquivoExcel.objects.create(
                percurso=percurso,
                nome="Etapa Teste",
                data_corrida="01/05/2026",
                local="Sorocaba",
                arquivo="uploads/etapa-teste.xlsx",
            )
        finally:
            post_save.connect(extrair_dados_excel, sender=ArquivoExcel)

    def test_pontuacao_categoria_desce_quando_atleta_ja_pontuou_no_geral(self):
        arquivo = self.criar_arquivo_resultado()

        for posicao in range(1, 12):
            Corredor.objects.create(
                arquivo=arquivo,
                colocacao=posicao,
                numero=str(posicao),
                nome=f"Atleta {posicao}",
                categoria="M 35-39",
                tempo_segundos=float(posicao),
                tempo_formatado=f"00:{posicao:02d}:00",
                Vel_media="12 km/h",
            )

        ranking_geral, ranking_categoria = calcular_rankinkg()

        self.assertEqual(ranking_geral["M"][0]["nome"], "Atleta 1")
        self.assertEqual(ranking_geral["M"][0]["pontos"], 21)
        self.assertEqual(ranking_categoria["35 - 39"]["M"][0]["nome"], "Atleta 6")
        self.assertEqual(ranking_categoria["35 - 39"]["M"][0]["pontos"], 10)
        self.assertEqual(ranking_categoria["35 - 39"]["M"][4]["nome"], "Atleta 10")
        self.assertEqual(ranking_categoria["35 - 39"]["M"][4]["pontos"], 2)
        self.assertEqual(ranking_categoria["35 - 39"]["M"][5]["nome"], "Atleta 11")
        self.assertEqual(ranking_categoria["35 - 39"]["M"][5]["pontos"], 1)

    def test_ranking_geral_feminino_considera_apenas_mulheres(self):
        arquivo = self.criar_arquivo_resultado()

        for posicao in range(1, 27):
            Corredor.objects.create(
                arquivo=arquivo,
                colocacao=posicao,
                numero=str(posicao),
                nome=f"Homem {posicao}",
                categoria="35 a 39 anos - Masculino",
                tempo_segundos=float(posicao),
                tempo_formatado=f"00:{posicao:02d}:00",
                Vel_media="12 km/h",
            )

        for indice, posicao in enumerate(range(27, 33), start=1):
            Corredor.objects.create(
                arquivo=arquivo,
                colocacao=posicao,
                numero=str(posicao),
                nome=f"Mulher {indice}",
                categoria="40 a 44 anos - Feminino",
                tempo_segundos=float(posicao),
                tempo_formatado=f"00:{posicao:02d}:00",
                Vel_media="12 km/h",
            )

        ranking_geral, ranking_categoria = calcular_rankinkg()

        self.assertEqual(ranking_geral["M"][0]["nome"], "Homem 1")
        self.assertEqual(ranking_geral["M"][0]["pontos"], 21)
        self.assertEqual(ranking_geral["F"][0]["nome"], "Mulher 1")
        self.assertEqual(ranking_geral["F"][0]["pontos"], 21)
        self.assertEqual(ranking_geral["F"][4]["nome"], "Mulher 5")
        self.assertEqual(ranking_geral["F"][4]["pontos"], 12)
        self.assertEqual(ranking_categoria["40 - 44"]["F"][0]["nome"], "Mulher 6")
        self.assertEqual(ranking_categoria["40 - 44"]["F"][0]["pontos"], 10)


@override_settings(STORAGES=TEST_STORAGES)
class ResultadoPercursoTests(TestCase):
    def setUp(self):
        post_save.disconnect(extrair_dados_excel, sender=ArquivoExcel)
        self.corrida = Corrida.objects.create(
            nome="Corrida XYZ",
            local="Sorocaba",
            data=date.today(),
        )
        self.percurso_7k = PercursoCorrida.objects.create(
            corrida=self.corrida,
            nome="7 km",
            distancia_km=7,
        )
        self.percurso_10k = PercursoCorrida.objects.create(
            corrida=self.corrida,
            nome="10 km",
            distancia_km=10,
            ordem=1,
        )
        self.outra_corrida = Corrida.objects.create(
            nome="Outra Corrida",
            local="Itu",
            data=date.today(),
        )
        self.percurso_outra_corrida = PercursoCorrida.objects.create(
            corrida=self.outra_corrida,
            nome="5 km",
            distancia_km=5,
        )

    def tearDown(self):
        post_save.connect(extrair_dados_excel, sender=ArquivoExcel)

    def test_cada_percurso_tem_um_arquivo_resultado_independente(self):
        resultado_7k = ArquivoExcel.objects.create(
            percurso=self.percurso_7k,
            nome="Resultados 7 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-7k.xlsx",
        )
        resultado_10k = ArquivoExcel.objects.create(
            percurso=self.percurso_10k,
            nome="Resultados 10 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-10k.xlsx",
        )

        self.assertEqual(resultado_7k.percurso, self.percurso_7k)
        self.assertEqual(resultado_10k.percurso, self.percurso_10k)
        self.assertEqual(self.percurso_7k.resultado, resultado_7k)
        self.assertEqual(self.percurso_10k.resultado, resultado_10k)

    def test_percurso_nao_aceita_mais_de_um_arquivo_resultado(self):
        ArquivoExcel.objects.create(
            percurso=self.percurso_7k,
            nome="Resultados 7 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-7k.xlsx",
        )

        with self.assertRaises(IntegrityError):
            ArquivoExcel.objects.create(
                percurso=self.percurso_7k,
                nome="Resultados 7 km duplicado",
                data_corrida="01/05/2026",
                local="Sorocaba",
                arquivo="uploads/resultado-7k-duplicado.xlsx",
            )

    def test_admin_form_resultado_exibe_corrida_e_percurso_opcional(self):
        form = ArquivoExcelAdminForm()

        self.assertIn("corrida", form.fields)
        self.assertFalse(form.fields["percurso"].required)
        self.assertIn(self.percurso_7k, form.fields["percurso"].queryset)
        self.assertIn(self.percurso_10k, form.fields["percurso"].queryset)
        self.assertIn(self.percurso_outra_corrida, form.fields["percurso"].queryset)

    def test_admin_resultado_usa_autocomplete_para_percurso(self):
        model_admin = ArquivoExcelAdmin(ArquivoExcel, admin.site)

        self.assertEqual(model_admin.autocomplete_fields, ("corrida", "percurso"))

    def test_admin_form_resultado_editando_usa_corrida_do_percurso_atual(self):
        resultado = ArquivoExcel.objects.create(
            percurso=self.percurso_7k,
            nome="Resultados 7 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-7k.xlsx",
        )

        form = ArquivoExcelAdminForm(instance=resultado)

        self.assertIn("corrida", form.fields)
        self.assertIn(self.percurso_7k, form.fields["percurso"].queryset)
        self.assertNotIn(self.percurso_outra_corrida, form.fields["percurso"].queryset)

    def test_resultado_pode_ser_criado_com_corrida_sem_percurso(self):
        resultado = ArquivoExcel.objects.create(
            corrida=self.corrida,
            nome="Resultado Geral",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-geral.xlsx",
        )

        self.assertEqual(resultado.corrida, self.corrida)
        self.assertIsNone(resultado.percurso)

    def test_resultado_exige_corrida_ou_percurso(self):
        resultado = ArquivoExcel(
            nome="Resultado sem vinculo",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo=EXCEL_TEST_FILE,
        )

        with self.assertRaisesMessage(ValidationError, "Informe a corrida ou o percurso do resultado."):
            resultado.full_clean()

    def test_resultado_com_percurso_define_corrida_do_percurso(self):
        resultado = ArquivoExcel.objects.create(
            percurso=self.percurso_7k,
            nome="Resultados 7 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-7k.xlsx",
        )

        self.assertEqual(resultado.corrida, self.corrida)

    def test_resultado_rejeita_corrida_diferente_do_percurso(self):
        resultado = ArquivoExcel(
            corrida=self.outra_corrida,
            percurso=self.percurso_7k,
            nome="Resultado inconsistente",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo=EXCEL_TEST_FILE,
        )

        with self.assertRaisesMessage(ValidationError, "Percurso invalido para esta corrida."):
            resultado.full_clean()

    def test_excluir_percurso_remove_apenas_inscricoes_do_percurso(self):
        user_7k = User.objects.create_user(username="atleta-7k")
        participante_7k = Participante.objects.create(
            usuario=user_7k,
            nome="Atleta 7k",
            cpf="11111111111",
            data_nascimento=date(1990, 1, 1),
            sexo="M",
            tamanho_camisa="M",
        )
        user_10k = User.objects.create_user(username="atleta-10k")
        participante_10k = Participante.objects.create(
            usuario=user_10k,
            nome="Atleta 10k",
            cpf="22222222222",
            data_nascimento=date(1991, 1, 1),
            sexo="F",
            tamanho_camisa="P",
        )
        inscricao_7k = Inscricao.objects.create(
            participante=participante_7k,
            corrida=self.corrida,
            percurso=self.percurso_7k,
        )
        inscricao_10k = Inscricao.objects.create(
            participante=participante_10k,
            corrida=self.corrida,
            percurso=self.percurso_10k,
        )

        self.percurso_7k.delete()

        self.assertFalse(PercursoCorrida.objects.filter(pk=self.percurso_7k.pk).exists())
        self.assertFalse(Inscricao.objects.filter(pk=inscricao_7k.pk).exists())
        self.assertTrue(Inscricao.objects.filter(pk=inscricao_10k.pk).exists())
        self.assertTrue(Participante.objects.filter(pk=participante_7k.pk).exists())
        self.assertTrue(User.objects.filter(pk=user_7k.pk).exists())
        self.assertTrue(Participante.objects.filter(pk=participante_10k.pk).exists())
        self.assertTrue(User.objects.filter(pk=user_10k.pk).exists())

    def test_excluir_percurso_com_resultado_publicado_remove_resultado_dependente(self):
        resultado = ArquivoExcel.objects.create(
            percurso=self.percurso_7k,
            nome="Resultados 7 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-7k.xlsx",
        )
        corredor = Corredor.objects.create(
            arquivo=resultado,
            colocacao=1,
            numero="7",
            nome="Atleta Sete",
            categoria="M 30-39",
            distancia="7 km",
            tempo_formatado="00:30:00",
            Vel_media="14 km/h",
        )

        self.percurso_7k.delete()

        self.assertFalse(PercursoCorrida.objects.filter(pk=self.percurso_7k.pk).exists())
        self.assertFalse(ArquivoExcel.objects.filter(pk=resultado.pk).exists())
        self.assertFalse(Corredor.objects.filter(pk=corredor.pk).exists())
        self.assertTrue(Corrida.objects.filter(pk=self.corrida.pk).exists())

    def test_excluir_resultado_nao_exclui_evento(self):
        resultado = ArquivoExcel.objects.create(
            percurso=self.percurso_7k,
            nome="Resultados 7 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-7k.xlsx",
        )

        resultado.delete()

        self.assertFalse(ArquivoExcel.objects.filter(pk=resultado.pk).exists())
        self.assertTrue(Corrida.objects.filter(pk=self.corrida.pk).exists())
        self.assertTrue(PercursoCorrida.objects.filter(pk=self.percurso_7k.pk).exists())

    def test_admin_exclui_evento_com_resultados_em_cascata(self):
        resultado_percurso = ArquivoExcel.objects.create(
            percurso=self.percurso_7k,
            nome="Resultados 7 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-7k.xlsx",
        )
        resultado_geral = ArquivoExcel.objects.create(
            corrida=self.corrida,
            nome="Resultado Geral",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-geral.xlsx",
        )
        corredor = Corredor.objects.create(
            arquivo=resultado_percurso,
            colocacao=1,
            numero="7",
            nome="Atleta Sete",
            categoria="M 30-39",
            distancia="7 km",
            tempo_formatado="00:30:00",
            Vel_media="14 km/h",
        )
        request = RequestFactory().post("/admin/api_s/corrida/")
        request.user = User.objects.create_superuser(
            username="admin-delete-evento",
            email="admin-delete@example.com",
            password="SenhaForte123!",
        )
        model_admin = CorridaAdmin(Corrida, admin.site)

        model_admin.delete_model(request, self.corrida)

        self.assertFalse(Corrida.objects.filter(pk=self.corrida.pk).exists())
        self.assertFalse(PercursoCorrida.objects.filter(pk=self.percurso_7k.pk).exists())
        self.assertFalse(PercursoCorrida.objects.filter(pk=self.percurso_10k.pk).exists())
        self.assertFalse(ArquivoExcel.objects.filter(pk=resultado_percurso.pk).exists())
        self.assertFalse(ArquivoExcel.objects.filter(pk=resultado_geral.pk).exists())
        self.assertFalse(Corredor.objects.filter(pk=corredor.pk).exists())
        self.assertTrue(Corrida.objects.filter(pk=self.outra_corrida.pk).exists())

    def test_pagina_corrida_filtra_resultado_unico_por_distancia(self):
        resultado = ArquivoExcel.objects.create(
            corrida=self.corrida,
            nome="Resultado Unico",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-unico.xlsx",
        )
        Corredor.objects.create(
            arquivo=resultado,
            colocacao=1,
            numero="7",
            nome="Atleta Sete",
            categoria="M 30-39",
            distancia="7 km",
            tempo_formatado="00:30:00",
            Vel_media="14 km/h",
        )
        Corredor.objects.create(
            arquivo=resultado,
            colocacao=1,
            numero="10",
            nome="Atleta Dez",
            categoria="M 30-39",
            distancia="10 km",
            tempo_formatado="00:45:00",
            Vel_media="13 km/h",
        )

        response = self.client.get(reverse("corrida_resultados", args=[self.corrida.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Distancia")
        self.assertContains(response, "7 km")
        self.assertContains(response, "10 km")
        self.assertContains(response, "Atleta Dez")
        self.assertNotContains(response, "Atleta Sete")

        response = self.client.get(
            reverse("corrida_resultados", args=[self.corrida.id]),
            {"distancia": "7 km"},
        )

        self.assertContains(response, "Atleta Sete")
        self.assertNotContains(response, "Atleta Dez")

    def test_pagina_corrida_exibe_resultado_sem_percurso(self):
        ArquivoExcel.objects.create(
            corrida=self.corrida,
            nome="Resultado Geral",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-geral.xlsx",
        )

        response = self.client.get(reverse("corrida_resultados", args=[self.corrida.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resultado Geral")

    def test_pagina_resultado_filtra_atletas_por_corrida_e_percurso(self):
        resultado_7k = ArquivoExcel.objects.create(
            percurso=self.percurso_7k,
            nome="Resultados 7 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-7k.xlsx",
        )
        resultado_10k = ArquivoExcel.objects.create(
            percurso=self.percurso_10k,
            nome="Resultados 10 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-10k.xlsx",
        )
        Corredor.objects.create(
            arquivo=resultado_7k,
            colocacao=1,
            numero="7",
            nome="Atleta Sete",
            categoria="M 30-39",
            distancia="7 km",
            tempo_formatado="00:30:00",
            Vel_media="14 km/h",
        )
        Corredor.objects.create(
            arquivo=resultado_10k,
            colocacao=1,
            numero="10",
            nome="Atleta Dez",
            categoria="M 30-39",
            distancia="10 km",
            tempo_formatado="00:45:00",
            Vel_media="13 km/h",
        )

        response = self.client.get(
            reverse("resultado_percurso_detail", args=[self.corrida.id, self.percurso_7k.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atleta Sete")
        self.assertNotContains(response, "Atleta Dez")

        response = self.client.get(
            reverse("resultado_percurso_detail", args=[self.corrida.id, self.percurso_10k.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atleta Dez")
        self.assertNotContains(response, "Atleta Sete")

    def test_filtro_distancia_troca_resultado_exibido(self):
        resultado = ArquivoExcel.objects.create(
            corrida=self.corrida,
            nome="Resultado Unico",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-unico.xlsx",
        )
        Corredor.objects.create(
            arquivo=resultado,
            colocacao=1,
            numero="7",
            nome="Homem 7k",
            categoria="M 30-39",
            distancia="7 km",
            tempo_formatado="00:30:00",
            Vel_media="14 km/h",
        )
        Corredor.objects.create(
            arquivo=resultado,
            colocacao=1,
            numero="10",
            nome="Atleta 10k",
            categoria="M 30-39",
            distancia="10 km",
            tempo_formatado="00:45:00",
            Vel_media="13 km/h",
        )

        response = self.client.get(
            reverse("corrida_resultados", args=[self.corrida.id]),
            {"distancia": "10 km"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atleta 10k")
        self.assertNotContains(response, "Homem 7k")
        self.assertContains(response, "Distancia")
        self.assertContains(response, "10 km")

    def test_colocacao_exibida_recalcula_depois_dos_filtros_sem_alterar_banco(self):
        resultado = ArquivoExcel.objects.create(
            corrida=self.corrida,
            nome="Resultado Unico",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-unico.xlsx",
        )
        atleta_lento = Corredor.objects.create(
            arquivo=resultado,
            colocacao=67,
            numero="501",
            nome="Atleta Teste Lento",
            categoria="15 a 19 anos - Masculino",
            distancia="5 km",
            tempo_segundos=1800,
            tempo_formatado="00:30:00",
            Vel_media="10 km/h",
        )
        atleta_rapido = Corredor.objects.create(
            arquivo=resultado,
            colocacao=68,
            numero="502",
            nome="Atleta Teste Rapido",
            categoria="15 a 19 anos - Masculino",
            distancia="5 km",
            tempo_segundos=1700,
            tempo_formatado="00:28:20",
            Vel_media="11 km/h",
        )
        Corredor.objects.create(
            arquivo=resultado,
            colocacao=1,
            numero="1001",
            nome="Atleta Outra Distancia",
            categoria="15 a 19 anos - Masculino",
            distancia="10 km",
            tempo_segundos=1600,
            tempo_formatado="00:26:40",
            Vel_media="12 km/h",
        )
        Corredor.objects.create(
            arquivo=resultado,
            colocacao=69,
            numero="503",
            nome="Atleta Teste Feminina",
            categoria="15 a 19 anos - Feminino",
            distancia="5 km",
            tempo_segundos=1650,
            tempo_formatado="00:27:30",
            Vel_media="11 km/h",
        )

        response = self.client.get(
            reverse("corrida_resultados", args=[self.corrida.id]),
            {
                "distancia": "5 km",
                "sexo": "M",
                "categoria": "15 a 19 anos",
                "nome": "Atleta Teste",
            },
        )

        corredores_exibidos = list(response.context["page"].object_list)
        self.assertEqual([corredor.nome for corredor in corredores_exibidos], [
            "Atleta Teste Rapido",
            "Atleta Teste Lento",
        ])
        self.assertEqual([corredor.colocacao_exibicao for corredor in corredores_exibidos], [1, 2])
        self.assertContains(response, "Atleta Teste Rapido")
        self.assertContains(response, "Atleta Teste Lento")
        self.assertNotContains(response, "Atleta Outra Distancia")
        self.assertNotContains(response, "Atleta Teste Feminina")

        atleta_lento.refresh_from_db()
        atleta_rapido.refresh_from_db()
        self.assertEqual(atleta_lento.colocacao, 67)
        self.assertEqual(atleta_rapido.colocacao, 68)

    def test_filtro_categoria_exibe_faixa_sem_sexo(self):
        resultado = ArquivoExcel.objects.create(
            corrida=self.corrida,
            nome="Resultado Unico",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-unico.xlsx",
        )
        Corredor.objects.create(
            arquivo=resultado,
            colocacao=1,
            numero="1",
            nome="Atleta Masculino",
            categoria="15 a 19 anos - Masculino",
            distancia="5 km",
            tempo_formatado="00:20:00",
            Vel_media="12 km/h",
        )
        Corredor.objects.create(
            arquivo=resultado,
            colocacao=2,
            numero="2",
            nome="Atleta Feminina",
            categoria="15 a 19 anos - Feminino",
            distancia="5 km",
            tempo_formatado="00:22:00",
            Vel_media="11 km/h",
        )

        filtro = CorredorFilter(queryset=Corredor.objects.filter(arquivo=resultado))
        categorias = list(filtro.filters["categoria"].extra["choices"])

        self.assertEqual(categorias, [("15 a 19 anos", "15 a 19 anos")])
        self.assertEqual(categoria_sem_sexo("15 a 19 anos - Masculino"), "15 a 19 anos")

    def test_filtro_sexo_e_categoria_nao_permite_combinacao_contraditoria(self):
        resultado = ArquivoExcel.objects.create(
            corrida=self.corrida,
            nome="Resultado Unico",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-unico.xlsx",
        )
        Corredor.objects.create(
            arquivo=resultado,
            colocacao=1,
            numero="1",
            nome="Atleta Masculino",
            categoria="15 a 19 anos - Masculino",
            distancia="5 km",
            tempo_formatado="00:20:00",
            Vel_media="12 km/h",
        )
        Corredor.objects.create(
            arquivo=resultado,
            colocacao=2,
            numero="2",
            nome="Atleta Feminina",
            categoria="15 a 19 anos - Feminino",
            distancia="5 km",
            tempo_formatado="00:22:00",
            Vel_media="11 km/h",
        )

        response = self.client.get(
            reverse("corrida_resultados", args=[self.corrida.id]),
            {"sexo": "F", "categoria": "15 a 19 anos"},
        )

        self.assertContains(response, "Atleta Feminina")
        self.assertNotContains(response, "Atleta Masculino")
        self.assertNotContains(response, "15 a 19 anos - Feminino")
        self.assertContains(response, "15 a 19 anos")

    def test_listagem_publica_mostra_um_card_por_corrida(self):
        ArquivoExcel.objects.create(
            percurso=self.percurso_7k,
            nome="Resultados 7 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-7k.xlsx",
        )
        ArquivoExcel.objects.create(
            percurso=self.percurso_10k,
            nome="Resultados 10 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-10k.xlsx",
        )

        response = self.client.get(reverse("arquivo_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.corrida.nome, count=1)

    def test_normaliza_distancia_do_excel(self):
        self.assertEqual(normalizar_distancia("10 Km"), "10")
        self.assertEqual(normalizar_distancia("10 km"), "10")
        self.assertEqual(normalizar_distancia("10KM"), "10")
        self.assertEqual(normalizar_distancia("10"), "10")
        self.assertEqual(normalizar_distancia("10.0"), "10")
        self.assertEqual(normalizar_distancia("10,0"), "10")

    def test_importacao_excel_associa_linhas_a_percursos_cadastrados(self):
        post_save.connect(extrair_dados_excel, sender=ArquivoExcel)
        try:
            ArquivoExcel.objects.create(
                corrida=self.corrida,
                nome="Resultado Unico",
                data_corrida="01/05/2026",
                local="Sorocaba",
                arquivo=excel_upload(
                    "resultado.xlsx",
                    [
                        {
                            "#": 1,
                            "N": "101",
                            "Nome": "Atleta 7",
                            "Categoria": "15 a 19 anos - Masculino",
                            "Distancia": "7 Km",
                            "Tempo": "00:30:00",
                            "Vel. Media": "14 km/h",
                        },
                        {
                            "#": 1,
                            "N": "201",
                            "Nome": "Atleta 10",
                            "Categoria": "15 a 19 anos - Feminino",
                            "Distancia": "10,0",
                            "Tempo": "00:45:00",
                            "Vel. Media": "13 km/h",
                        },
                    ],
                ),
            )
        finally:
            post_save.disconnect(extrair_dados_excel, sender=ArquivoExcel)

        self.assertTrue(
            Corredor.objects.filter(nome="Atleta 7", distancia=self.percurso_7k.nome).exists()
        )
        self.assertTrue(
            Corredor.objects.filter(nome="Atleta 10", distancia=self.percurso_10k.nome).exists()
        )

    def test_importacao_excel_rejeita_distancia_sem_percurso_cadastrado(self):
        post_save.connect(extrair_dados_excel, sender=ArquivoExcel)
        try:
            with self.assertRaisesMessage(ValueError, "nao existe como percurso cadastrado"):
                ArquivoExcel.objects.create(
                    corrida=self.corrida,
                    nome="Resultado Invalido",
                    data_corrida="01/05/2026",
                    local="Sorocaba",
                    arquivo=excel_upload(
                        "resultado-invalido.xlsx",
                        [
                            {
                                "#": 1,
                                "N": "301",
                                "Nome": "Atleta 21",
                                "Categoria": "15 a 19 anos - Masculino",
                                "Distância (Km)": "21KM",
                                "Tempo": "01:30:00",
                                "Vel. Media": "10 km/h",
                            },
                        ],
                    ),
                )
        finally:
            post_save.disconnect(extrair_dados_excel, sender=ArquivoExcel)

        self.assertFalse(Corredor.objects.filter(nome="Atleta 21").exists())

    def test_importacao_excel_sem_distancia_nao_usa_geral_se_corrida_tem_percurso(self):
        post_save.connect(extrair_dados_excel, sender=ArquivoExcel)
        try:
            with self.assertRaisesMessage(ValueError, "nao possui coluna Distancia"):
                ArquivoExcel.objects.create(
                    corrida=self.corrida,
                    nome="Resultado Sem Distancia",
                    data_corrida="01/05/2026",
                    local="Sorocaba",
                    arquivo=excel_upload(
                        "resultado-sem-distancia.xlsx",
                        [
                            {
                                "#": 1,
                                "N": "401",
                                "Nome": "Atleta Sem Distancia",
                                "Categoria": "15 a 19 anos - Masculino",
                                "Tempo": "00:30:00",
                                "Vel. Media": "14 km/h",
                            },
                        ],
                    ),
                )
        finally:
            post_save.disconnect(extrair_dados_excel, sender=ArquivoExcel)

        self.assertFalse(Corredor.objects.filter(nome="Atleta Sem Distancia").exists())

    def test_pagina_resultado_rejeita_percurso_de_outra_corrida(self):
        ArquivoExcel.objects.create(
            percurso=self.percurso_outra_corrida,
            nome="Resultado Outra Corrida",
            data_corrida="01/05/2026",
            local="Itu",
            arquivo="uploads/resultado-outra.xlsx",
        )

        response = self.client.get(
            reverse("resultado_percurso_detail", args=[self.corrida.id, self.percurso_outra_corrida.id])
        )

        self.assertEqual(response.status_code, 404)

    def test_corrida_com_um_percurso_redireciona_para_resultado_do_percurso(self):
        corrida_unica = Corrida.objects.create(
            nome="Corrida Unica",
            local="Sorocaba",
            data=date.today(),
        )
        percurso_unico = PercursoCorrida.objects.create(
            corrida=corrida_unica,
            nome="Percurso unico",
            distancia_km=5,
        )
        ArquivoExcel.objects.create(
            percurso=percurso_unico,
            nome="Resultado Unico",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-unico.xlsx",
        )

        Corredor.objects.create(
            arquivo=ArquivoExcel.objects.get(nome="Resultado Unico"),
            colocacao=1,
            numero="1",
            nome="Atleta Unico",
            categoria="M 30-39",
            distancia="5 km",
            tempo_formatado="00:25:00",
            Vel_media="12 km/h",
        )

        response = self.client.get(reverse("corrida_resultados", args=[corrida_unica.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atleta Unico")
        self.assertNotContains(response, "Distancia")


@override_settings(CRONOMETRAGEM_API_KEY="test-api-key")
class CronometragemAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username=CPF_VALIDO,
            password="SenhaForte123!",
            first_name="Joao Corredor",
        )
        self.participante = Participante.objects.create(
            usuario=self.user,
            nome="Joao Corredor",
            cpf=CPF_VALIDO,
            data_nascimento=date(1991, 4, 20),
            sexo="M",
            tamanho_camisa="G",
            cidade="Sorocaba",
            equipe="Equipe A",
        )
        self.corrida = Corrida.objects.create(
            nome="Corrida API",
            local="Sorocaba",
            data=date.today() + timedelta(days=10),
        )
        self.percurso = PercursoCorrida.objects.create(
            corrida=self.corrida,
            nome="5 km",
            distancia_km=5,
        )
        self.inscricao_paga = Inscricao.objects.create(
            participante=self.participante,
            corrida=self.corrida,
            percurso=self.percurso,
            pago=True,
        )

        self.participante_nao_pago = Participante.objects.create(
            nome="Atleta Nao Pago",
            cpf=CPF_VALIDO_2,
            data_nascimento=date(1995, 1, 1),
            sexo="F",
            tamanho_camisa="P",
            cidade="Itu",
        )
        self.inscricao_nao_paga = Inscricao.objects.create(
            participante=self.participante_nao_pago,
            corrida=self.corrida,
            percurso=self.percurso,
            pago=False,
        )
        self.auth = {"HTTP_AUTHORIZATION": "Api-Key test-api-key"}

    def test_api_nega_acesso_sem_chave_valida(self):
        response = self.client.get(reverse("api_cronometragem_eventos"))
        self.assertEqual(response.status_code, 401)

        response = self.client.get(
            reverse("api_cronometragem_eventos"),
            HTTP_AUTHORIZATION="Api-Key errada",
        )
        self.assertEqual(response.status_code, 401)

    def test_lista_eventos_e_percursos(self):
        response = self.client.get(reverse("api_cronometragem_eventos"), **self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["id"], self.corrida.id)

        response = self.client.get(
            reverse("api_cronometragem_percursos", args=[self.corrida.id]),
            **self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["id"], self.percurso.id)

    def test_lista_apenas_inscricoes_pagas_do_evento_e_percurso(self):
        response = self.client.get(
            reverse("api_cronometragem_inscricoes_pagas"),
            {
                "evento_id": self.corrida.id,
                "percurso_id": self.percurso.id,
            },
            **self.auth,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], self.inscricao_paga.id)
        self.assertEqual(data[0]["nome_atleta"], "Joao Corredor")
        self.assertNotIn("cpf", data[0])

    def test_atualiza_numero_chip_apenas_para_inscricao_paga(self):
        response = self.client.patch(
            reverse("api_cronometragem_inscricao_chip", args=[self.inscricao_nao_paga.id]),
            data={"numero_chip": "123"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.patch(
            reverse("api_cronometragem_inscricao_chip", args=[self.inscricao_paga.id]),
            data={"numero_chip": "123"},
            content_type="application/json",
            **self.auth,
        )

        self.assertEqual(response.status_code, 200)
        self.inscricao_paga.refresh_from_db()
        self.assertEqual(self.inscricao_paga.numero_chip, "123")

    def test_envia_resultado_cria_e_atualiza_por_inscricao(self):
        payload = {
            "inscricao_id": self.inscricao_paga.id,
            "tempo": "00:25:10.123",
            "posicao_geral": 1,
            "colocacao_categoria": 1,
        }
        response = self.client.post(
            reverse("api_cronometragem_resultados"),
            data=payload,
            content_type="application/json",
            **self.auth,
        )

        self.assertEqual(response.status_code, 200)
        resultado = ResultadoInscricao.objects.get(inscricao=self.inscricao_paga)
        self.assertEqual(resultado.tempo, "00:25:10.123")

        payload["tempo"] = "00:25:09.900"
        response = self.client.post(
            reverse("api_cronometragem_resultados"),
            data=payload,
            content_type="application/json",
            **self.auth,
        )

        self.assertEqual(response.status_code, 200)
        resultado.refresh_from_db()
        self.assertEqual(resultado.tempo, "00:25:09.900")
        self.assertEqual(ResultadoInscricao.objects.filter(inscricao=self.inscricao_paga).count(), 1)


@override_settings(
    CRONOMETRAGEM_RECEIVER_URL="http://127.0.0.1:8001/api/inscricoes/receber",
    CRONOMETRAGEM_RECEIVER_API_KEY="secret-api-key",
    CRONOMETRAGEM_RECEIVER_TIMEOUT=2,
)
class CronometragemReceiverEnvioTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_user = User.objects.create_superuser(
            username="admin-cronometragem",
            email="admin@example.com",
            password="SenhaForte123!",
        )
        self.corrida = Corrida.objects.create(
            nome="Corrida Receiver",
            local="Sorocaba",
            data=date.today() + timedelta(days=10),
        )
        self.percurso = PercursoCorrida.objects.create(
            corrida=self.corrida,
            nome="5 km",
            distancia_km=5,
        )
        self._cpf_indice = 10000000000

    def criar_participante(self, nome="Atleta Receiver"):
        self._cpf_indice += 1
        return Participante.objects.create(
            nome=nome,
            cpf=str(self._cpf_indice),
            data_nascimento=date(1990, 1, 1),
            sexo="M",
            tamanho_camisa="M",
            cidade="Sao Paulo",
            equipe="Equipe X",
        )

    def criar_inscricao(self, pago=False, enviada=False, nome="Atleta Receiver"):
        return Inscricao.objects.create(
            participante=self.criar_participante(nome=nome),
            corrida=self.corrida,
            percurso=self.percurso,
            pago=pago,
            enviada_cronometragem=enviada,
        )

    def criar_inscricao_sem_disparo(self, pago=False, enviada=False, nome="Atleta Receiver"):
        inscricao = self.criar_inscricao(pago=False, enviada=False, nome=nome)
        Inscricao.objects.filter(pk=inscricao.pk).update(
            pago=pago,
            enviada_cronometragem=enviada,
        )
        inscricao.refresh_from_db()
        return inscricao

    def test_inscricao_paga_e_enviada_automaticamente(self):
        with patch("eventos.services.cronometragem_client.requests.post") as post:
            post.return_value = Mock(status_code=201, text="")

            with self.captureOnCommitCallbacks(execute=True):
                inscricao = self.criar_inscricao(pago=True)

        post.assert_called_once()
        _, kwargs = post.call_args
        self.assertEqual(
            post.call_args[0][0],
            "http://127.0.0.1:8001/api/inscricoes/receber",
        )
        self.assertEqual(kwargs["headers"]["Authorization"], "Api-Key secret-api-key")
        self.assertEqual(kwargs["timeout"], 2)
        self.assertEqual(kwargs["json"]["inscricao_id"], inscricao.id)
        self.assertEqual(kwargs["json"]["evento_id"], self.corrida.id)
        self.assertEqual(kwargs["json"]["evento_nome"], self.corrida.nome)
        self.assertEqual(kwargs["json"]["percurso_id"], self.percurso.id)
        self.assertEqual(kwargs["json"]["percurso_nome"], "5 Km")
        self.assertEqual(kwargs["json"]["nome"], "Atleta Receiver")
        self.assertEqual(kwargs["json"]["sexo"], "Masculino")
        self.assertEqual(kwargs["json"]["categoria"], "35 a 39 anos")
        self.assertNotIn("cpf", kwargs["json"])
        self.assertNotIn("numero_chip", kwargs["json"])

        inscricao.refresh_from_db()
        self.assertTrue(inscricao.enviada_cronometragem)
        self.assertIsNotNone(inscricao.data_envio_cronometragem)
        self.assertEqual(inscricao.erro_envio_cronometragem, "")

    def test_inscricao_nao_paga_nao_e_enviada_automaticamente(self):
        with patch("eventos.services.cronometragem_client.requests.post") as post:
            with self.captureOnCommitCallbacks(execute=True):
                inscricao = self.criar_inscricao(pago=False)

        post.assert_not_called()
        inscricao.refresh_from_db()
        self.assertFalse(inscricao.enviada_cronometragem)

    def test_inscricao_ja_enviada_nao_e_reenviada_automaticamente(self):
        with patch("eventos.services.cronometragem_client.requests.post") as post:
            with self.captureOnCommitCallbacks(execute=True):
                inscricao = self.criar_inscricao(pago=True, enviada=True)

        post.assert_not_called()
        inscricao.refresh_from_db()
        self.assertTrue(inscricao.enviada_cronometragem)

    def test_alteracao_relevante_em_inscricao_ja_enviada_reenvia_mesmo_inscricao_id(self):
        inscricao = self.criar_inscricao_sem_disparo(pago=True, enviada=True)
        novo_percurso = PercursoCorrida.objects.create(
            corrida=self.corrida,
            nome="10 km",
            distancia_km=10,
        )

        with patch("eventos.services.cronometragem_client.requests.post") as post:
            post.return_value = Mock(status_code=200, text="")

            with self.captureOnCommitCallbacks(execute=True):
                inscricao.percurso = novo_percurso
                inscricao.save(update_fields=["percurso", "atualizada_em"])

        post.assert_called_once()
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["inscricao_id"], inscricao.id)
        self.assertEqual(kwargs["json"]["percurso_id"], novo_percurso.id)
        self.assertEqual(kwargs["json"]["percurso_nome"], "10 Km")
        self.assertNotIn("numero_chip", kwargs["json"])

    def test_alteracao_relevante_em_participante_reenvia_inscricoes_ja_enviadas(self):
        inscricao = self.criar_inscricao_sem_disparo(pago=True, enviada=True)
        participante = inscricao.participante

        with patch("eventos.services.cronometragem_client.requests.post") as post:
            post.return_value = Mock(status_code=200, text="")

            with self.captureOnCommitCallbacks(execute=True):
                participante.nome = "Atleta Atualizado"
                participante.cidade = "Campinas"
                participante.equipe = "Equipe Atualizada"
                participante.save(update_fields=["nome", "cidade", "equipe"])

        post.assert_called_once()
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["inscricao_id"], inscricao.id)
        self.assertEqual(kwargs["json"]["nome"], "Atleta Atualizado")
        self.assertEqual(kwargs["json"]["cidade"], "Campinas")
        self.assertEqual(kwargs["json"]["equipe"], "Equipe Atualizada")

    def test_alteracao_em_corrida_reenvia_inscricoes_ja_enviadas(self):
        inscricao = self.criar_inscricao_sem_disparo(pago=True, enviada=True)

        with patch("eventos.services.cronometragem_client.requests.post") as post:
            post.return_value = Mock(status_code=200, text="")

            with self.captureOnCommitCallbacks(execute=True):
                self.corrida.nome = "Corrida Atualizada"
                self.corrida.save(update_fields=["nome"])

        post.assert_called_once()
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["inscricao_id"], inscricao.id)
        self.assertEqual(kwargs["json"]["evento_nome"], "Corrida Atualizada")

    def test_alteracao_em_percurso_reenvia_inscricoes_ja_enviadas(self):
        inscricao = self.criar_inscricao_sem_disparo(pago=True, enviada=True)

        with patch("eventos.services.cronometragem_client.requests.post") as post:
            post.return_value = Mock(status_code=200, text="")

            with self.captureOnCommitCallbacks(execute=True):
                self.percurso.distancia_km = 7
                self.percurso.save(update_fields=["distancia_km"])

        post.assert_called_once()
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["inscricao_id"], inscricao.id)
        self.assertEqual(kwargs["json"]["percurso_nome"], "7 Km")

    def test_alteracao_de_chip_nao_reenvia_inscricao_ja_enviada(self):
        inscricao = self.criar_inscricao_sem_disparo(pago=True, enviada=True)

        with patch("eventos.services.cronometragem_client.requests.post") as post:
            with self.captureOnCommitCallbacks(execute=True):
                inscricao.numero_chip = "RFID-123"
                inscricao.save(update_fields=["numero_chip", "atualizada_em"])

        post.assert_not_called()

    def test_falha_de_conexao_nao_quebra_salvamento_e_nao_expoe_api_key(self):
        with patch("eventos.services.cronometragem_client.requests.post") as post:
            post.side_effect = requests_exceptions.ConnectionError(
                "FastAPI offline secret-api-key"
            )

            with self.assertLogs("eventos.services.cronometragem_client", level="WARNING") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    inscricao = self.criar_inscricao(pago=True)

        self.assertTrue(Inscricao.objects.filter(pk=inscricao.pk).exists())
        inscricao.refresh_from_db()
        self.assertFalse(inscricao.enviada_cronometragem)
        self.assertIsNone(inscricao.data_envio_cronometragem)
        self.assertIn("ConnectionError", inscricao.erro_envio_cronometragem)
        self.assertNotIn("secret-api-key", inscricao.erro_envio_cronometragem)
        self.assertNotIn("secret-api-key", "\n".join(logs.output))

    def test_falha_de_conexao_no_reenvio_mantem_inscricao_reenviavel(self):
        inscricao = self.criar_inscricao_sem_disparo(pago=True, enviada=True)
        participante = inscricao.participante

        with patch("eventos.services.cronometragem_client.requests.post") as post:
            post.side_effect = requests_exceptions.ConnectionError(
                "FastAPI offline secret-api-key"
            )

            with self.assertLogs("eventos.services.cronometragem_client", level="WARNING") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    participante.cidade = "Campinas"
                    participante.save(update_fields=["cidade"])

        inscricao.refresh_from_db()
        self.assertFalse(inscricao.enviada_cronometragem)
        self.assertIsNone(inscricao.data_envio_cronometragem)
        self.assertIn("ConnectionError", inscricao.erro_envio_cronometragem)
        self.assertNotIn("secret-api-key", inscricao.erro_envio_cronometragem)
        self.assertNotIn("secret-api-key", "\n".join(logs.output))

    def test_admin_action_reenvia_inscricoes_pagas_pendentes_e_ja_enviadas(self):
        pendente = self.criar_inscricao_sem_disparo(pago=True, nome="Pendente")
        nao_paga = self.criar_inscricao_sem_disparo(pago=False, nome="Nao Paga")
        enviada = self.criar_inscricao_sem_disparo(
            pago=True,
            enviada=True,
            nome="Ja Enviada",
        )
        request = self.factory.post("/admin/api_s/inscricao/")
        request.user = self.admin_user
        model_admin = InscricaoAdmin(Inscricao, admin.site)

        with patch("eventos.services.cronometragem_client.requests.post") as post:
            post.return_value = Mock(status_code=200, text="")
            with patch.object(model_admin, "message_user") as message_user:
                model_admin.reenviar_para_cronometragem(
                    request,
                    Inscricao.objects.filter(
                        pk__in=[pendente.pk, nao_paga.pk, enviada.pk]
                    ),
                )

        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            {call.kwargs["json"]["inscricao_id"] for call in post.call_args_list},
            {pendente.id, enviada.id},
        )
        pendente.refresh_from_db()
        nao_paga.refresh_from_db()
        enviada.refresh_from_db()
        self.assertTrue(pendente.enviada_cronometragem)
        self.assertFalse(nao_paga.enviada_cronometragem)
        self.assertTrue(enviada.enviada_cronometragem)

        mensagem = message_user.call_args[0][1]
        self.assertIn("2 enviadas", mensagem)
        self.assertIn("0 falharam", mensagem)
        self.assertIn("1 ignoradas", mensagem)
        self.assertNotIn("secret-api-key", mensagem)



