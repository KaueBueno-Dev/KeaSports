from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from datetime import datetime

from eventos.models import Participante, Corredor


MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def login_rate_limit_key(request, cpf):
    return f"login_attempts:{get_client_ip(request)}:{cpf}"


def is_login_rate_limited(request, cpf):
    attempts = cache.get(login_rate_limit_key(request, cpf), 0)
    return attempts >= MAX_LOGIN_ATTEMPTS


def register_failed_login(request, cpf):
    key = login_rate_limit_key(request, cpf)
    attempts = cache.get(key, 0) + 1
    cache.set(key, attempts, LOGIN_ATTEMPT_WINDOW_SECONDS)


def clear_failed_login(request, cpf):
    cache.delete(login_rate_limit_key(request, cpf))


def validar_cpf(cpf):
    cpf = ''.join(filter(str.isdigit, cpf))
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    digito1 = (soma * 10) % 11
    if digito1 == 10:
        digito1 = 0
    if digito1 != int(cpf[9]):
        return False

    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    digito2 = (soma * 10) % 11
    if digito2 == 10:
        digito2 = 0
    if digito2 != int(cpf[10]):
        return False
    return True


def register_view(request):

    alerta = None

    if request.method == "POST":

        nome = request.POST.get("nome_completo", "").strip()

        cpf = request.POST.get("cpf", "").strip()
        cpf = ''.join(filter(str.isdigit, cpf))

        password = request.POST.get("password", "").strip()
        confirmar_senha = request.POST.get("confirmar_senha", "").strip()

        data = request.POST.get("data_nascimento")

        sexo = request.POST.get("sexo", "").strip()
        tamanho_camisa = request.POST.get("tamanho_camisa", "M").strip()
        cidade = request.POST.get("cidade", "").strip() or None
        equipe = request.POST.get("equipe", "").strip() or None

        if not nome or not cpf or not password or not data:
            alerta = "Preencha todos os campos obrigatórios."

        elif not sexo or not tamanho_camisa:
            alerta = "Preencha todos os campos obrigatórios."

        elif password != confirmar_senha:
            alerta = "As senhas não coincidem."

        elif not validar_cpf(cpf):
            alerta = "CPF inválido."

        elif User.objects.filter(username=cpf).exists():
            alerta = "Esse CPF já está cadastrado."

        else:
            try:
                validate_password(password)
                data_nascimento = datetime.strptime(data, "%Y-%m-%d").date()

                with transaction.atomic():
                    user = User.objects.create_user(
                        username=cpf,
                        password=password,
                        first_name=nome
                    )

                    Participante.objects.create(
                        usuario=user,
                        nome=nome,
                        cpf=cpf,
                        data_nascimento=data_nascimento,
                        sexo=sexo,
                        tamanho_camisa=tamanho_camisa,
                        cidade=cidade,
                        equipe=equipe
                    )

                return redirect("cadastro")

            except IntegrityError:
                alerta = "Erro ao criar conta."

            except ValidationError as exc:
                alerta = " ".join(exc.messages)

            except ValueError:
                alerta = "Data de nascimento inválida."

    return render(request, "eventos/auth/register.html", {"alerta": alerta})


def cadastro(request):

    if request.method == "POST":

        cpf = request.POST.get("cpf", "").strip()
        cpf = ''.join(filter(str.isdigit, cpf))

        password = request.POST.get("password")

        if is_login_rate_limited(request, cpf):
            return render(
                request,
                "eventos/auth/cadastro.html",
                {"erro": "Muitas tentativas. Aguarde alguns minutos e tente novamente."}
            )

        user = authenticate(request, username=cpf, password=password)

        if user:
            clear_failed_login(request, cpf)
            login(request, user)
            return redirect("index")
        else:
            register_failed_login(request, cpf)
            return render(
                request,
                "eventos/auth/cadastro.html",
                {"erro": "CPF ou senha incorretos."}
            )

    return render(request, "eventos/auth/cadastro.html")


@csrf_protect
def auth_page(request):
    """View unificada de autenticação: login para não logados, painel para logados."""
    erro = None
    sucesso = None

    # Se usuário já está logado, exibe painel de logout
    if request.user.is_authenticated:
        participante = None
        try:
            participante = Participante.objects.get(usuario=request.user)
        except Participante.DoesNotExist:
            pass

        if request.method == "POST" and request.POST.get("logout"):
            logout(request)
            return redirect("auth_page")

        return render(
            request,
            "eventos/auth/auth_page.html",
            {
                "usuario_logado": True,
                "participante": participante,
                "sucesso": sucesso
            }
        )

    # Se não está logado, exibe formulário de login
    if request.method == "POST":
        cpf = request.POST.get("cpf", "").strip()
        cpf = ''.join(filter(str.isdigit, cpf))
        password = request.POST.get("password")

        if not cpf or not password:
            erro = "Preencha todos os campos."
        elif is_login_rate_limited(request, cpf):
            erro = "Muitas tentativas. Aguarde alguns minutos e tente novamente."
        else:
            user = authenticate(request, username=cpf, password=password)

            if user:
                clear_failed_login(request, cpf)
                login(request, user)
                # Redireciona com GET para evitar problemas de CSRF no POST
                return redirect("auth_page")
            else:
                register_failed_login(request, cpf)
                erro = "CPF ou senha incorretos."

    return render(
        request,
        "eventos/auth/auth_page.html",
        {"erro": erro}
    )


def historico_usuario(request):

    alerta = None
    historico = None
    atleta = None
    estatisticas = None

    if request.method == "POST":

        nome = request.POST.get("nome", "").strip()

        if not nome:
            alerta = "Preencha o campo."
        else:
            historico = (
                Corredor.objects
                .select_related("arquivo")
                .filter(nome__icontains=nome)
                .order_by("-arquivo__criado_em", "colocacao")
            )

            if not historico.exists():
                alerta = "Nome não encontrado."
            else:
                historico_lista = list(historico)
                total_provas = len({c.arquivo_id for c in historico_lista if c.arquivo_id})
                podios_arquivos = set()

                for resultado in historico_lista:
                    if not resultado.arquivo_id:
                        continue

                    ficou_top5_geral = resultado.colocacao and resultado.colocacao <= 5

                    # A posicao por categoria usa o mesmo criterio da pagina de resultados:
                    # quando ha tempo em segundos, ordena os atletas daquela categoria por tempo.
                    posicao_categoria = None
                    if resultado.categoria and resultado.tempo_segundos is not None:
                        categoria_ids = list(
                            Corredor.objects
                            .filter(
                                arquivo_id=resultado.arquivo_id,
                                categoria=resultado.categoria,
                                tempo_segundos__isnull=False,
                            )
                            .order_by("tempo_segundos", "colocacao")
                            .values_list("id", flat=True)
                        )

                        if resultado.id in categoria_ids:
                            posicao_categoria = categoria_ids.index(resultado.id) + 1

                    ficou_top5_categoria = posicao_categoria is not None and posicao_categoria <= 5

                    if ficou_top5_geral or ficou_top5_categoria:
                        podios_arquivos.add(resultado.arquivo_id)

                progresso_meta = 5
                progresso_atual = min(total_provas, progresso_meta)
                progresso_percentual = int((progresso_atual / progresso_meta) * 100) if progresso_meta else 0
                corridas_restantes = max(progresso_meta - progresso_atual, 0)

                atleta = {
                    "nome": historico_lista[0].nome,
                    "subtitulo": "Histórico de Corridas",
                    "local": next((c.arquivo.local for c in historico_lista if c.arquivo and c.arquivo.local), ""),
                }

                estatisticas = {
                    "total_provas": total_provas,
                    # Nao ha calendario completo de etapas no banco para medir lacunas entre provas.
                    # Por enquanto, a sequencia atual considera as provas registradas para o atleta.
                    "provas_seguidas": total_provas,
                    "podios": len(podios_arquivos),
                    "progresso_atual": progresso_atual,
                    "progresso_meta": progresso_meta,
                    "progresso_percentual": progresso_percentual,
                    "corridas_restantes": corridas_restantes,
                }

    return render(
        request,
        "eventos/participante/historico_usuario.html",
        {
            "historico": historico,
            "alerta": alerta,
            "atleta": atleta,
            "estatisticas": estatisticas,
        }
    )


def buscar_nomes_autocomplete(request):
    """View para autocomplete de nomes de corredores (busca parcial)."""
    termo = request.GET.get("q", "").strip()

    if not termo:
        return JsonResponse({"resultados": []})

    corredores = (
        Corredor.objects
        .filter(nome__icontains=termo)
        .values_list("nome", flat=True)
        .distinct()
        .order_by("nome")[:10]
    )

    return JsonResponse({"resultados": list(corredores)})


def logout_confirm(request):
    return render(request, "eventos/auth/logout.html")


@login_required
@require_POST
def logout_view(request):
    logout(request)
    return redirect("index")


@csrf_protect
@require_POST
def logout_rapido(request):
    """View para logout direto via POST, sem página de confirmação."""
    logout(request)
    return redirect("index")
