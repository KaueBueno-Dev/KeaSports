from datetime import date, timedelta
from io import BytesIO

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.db.models.signals import post_save
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from eventos.forms import ArquivoExcelAdminForm, InscricaoAdminForm
from eventos.admin import ADMIN_MODEL_ORDER, ArquivoExcelAdmin, CorridaAdmin
from eventos.filters import CorredorFilter, categoria_sem_sexo
from eventos.models import ArquivoExcel, Corredor, Corrida, Inscricao, Participante, PercursoCorrida, ResultadoInscricao
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
        self.client.login(username=CPF_VALIDO, password="SenhaForte123!")

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

    def test_criacao_de_corrida_nao_exibe_inlines_dependentes_de_corrida_salva(self):
        inlines = self.model_admin.get_inline_instances(self._request(), obj=None)

        self.assertEqual(inlines, [])

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
                "PercursoCorrida",
                "Participante",
                "Inscricao",
                "ArquivoExcel",
                "ResultadoInscricao",
                "Corredor",
            ],
        )


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

    def test_excluir_percurso_com_resultado_publicado_continua_protegido(self):
        ArquivoExcel.objects.create(
            percurso=self.percurso_7k,
            nome="Resultados 7 km",
            data_corrida="01/05/2026",
            local="Sorocaba",
            arquivo="uploads/resultado-7k.xlsx",
        )

        with self.assertRaises(ProtectedError):
            self.percurso_7k.delete()

        self.assertTrue(PercursoCorrida.objects.filter(pk=self.percurso_7k.pk).exists())

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
