from django.urls import path
from . import views
from . import api_views


urlpatterns = [
    path('', views.index, name='index'),

    # Corridas
    path('corridas/', views.listar_corridas, name='listar_corridas'),
    path('rankinkg/', views.rankinkg, name='rankinkg'),
    path('corridas/<int:corrida_id>/inscrever/', views.inscrever_corrida, name='inscrever_corrida'),
    path('eventos/buscar-cpf/', views.buscar_usuario_cpf, name='buscar_usuario_cpf'),

    # Rotas de participantes
    path('classificacao/', views.classificacao_view, name='classificacao'),

    path('corridas/<int:corrida_id>/resultados/', views.corrida_resultados, name='corrida_resultados'),
    path('corridas/<int:corrida_id>/resultados/<int:percurso_id>/', views.resultado_percurso_detail, name='resultado_percurso_detail'),
    path('arquivo/<int:pk>/', views.arquivo_detail, name='arquivo_detail'),
    path('arquivo_list/', views.ArquivoExcelListView, name='arquivo_list'),

    
    path('register/', views.register_view, name="register"),
    path('cadastro/', views.cadastro, name='cadastro'),
    path('auth/', views.auth_page, name='auth_page'),  # Página unificada login/logout
  
    path('logout/', views.logout_confirm, name='logout_confirm'),  # página de confirmação
    path('logout/confirm/', views.logout_view, name='logout'),     # ação real de logout
    path('logout-rapido/', views.logout_rapido, name='logout_rapido'),  # logout direto via POST

    path('historico_usuario/', views.historico_usuario, name='historico_usuario'),
    path('eventos/buscar-nomes/', views.buscar_nomes_autocomplete, name='buscar_nomes_autocomplete'),

    # API privada de cronometragem
    path('api/cronometragem/eventos/', api_views.EventosCronometragemView.as_view(), name='api_cronometragem_eventos'),
    path('api/cronometragem/eventos/<int:evento_id>/percursos/', api_views.PercursosCronometragemView.as_view(), name='api_cronometragem_percursos'),
    path('api/cronometragem/inscricoes-pagas/', api_views.InscricoesPagasCronometragemView.as_view(), name='api_cronometragem_inscricoes_pagas'),
    path('api/cronometragem/inscricoes/<int:inscricao_id>/chip/', api_views.AtualizarChipInscricaoView.as_view(), name='api_cronometragem_inscricao_chip'),
    path('api/cronometragem/resultados/', api_views.EnviarResultadoCronometragemView.as_view(), name='api_cronometragem_resultados'),

]                                                                               
