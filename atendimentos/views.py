import os
import base64
import openpyxl
import calendar
import operator
import traceback

# Imports de bibliotecas padrão
from datetime import datetime, time, timedelta

# Imports de bibliotecas de terceiros (third-party)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request as GoogleAuthRequest
from weasyprint import HTML
from functools import reduce
from collections import defaultdict
from dateutil.parser import parse as parse_datetime


# Imports do Django
from django.db.models.functions import Trim
from django.conf import settings
from django.contrib.sites.models import Site
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db.models import Count, Q, Value
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from itertools import chain

# Imports do Django REST Framework
from rest_framework import generics, permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.pagination import PageNumberPagination
from rest_framework.generics import ListAPIView

# Imports locais (do seu projeto)
from .models import *
from .permissions import (CanAccessContacts, CanAccessObjectByConta, CanViewSharedAgenda, CanAccessEspaco,
                          CanInteractWithAtendimento, CanManageAgendas, CanCreateGoogleEvent, CanManageReservas,
                          CanViewAgendaReports, CanViewAtendimentoReports, CanEditMunicipeDetails, CanManageCheckIn,
                          is_in_group)
from .serializers import *


# -----------------------------------------------------------------------------
# Views de Atendimento
# -----------------------------------------------------------------------------

class AtendimentoListCreateView(generics.ListCreateAPIView):
    serializer_class = AtendimentoSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        # REGRA 1: Superusuário vê tudo.
        if user.is_superuser:
            return Atendimento.objects.all().order_by('-data_criacao')
        
        # --- AQUI ESTÁ A CORREÇÃO PARA A RECEPÇÃO ---
        # REGRA 2: Se for da Recepção, mostra TODOS os atendimentos das contas vinculadas.
        if is_in_group(user, 'Recepção'):
            if hasattr(user, 'perfil'):
                return Atendimento.objects.filter(conta__in=user.perfil.contas.all()).order_by('-data_criacao')
            else:
                return Atendimento.objects.none() # Se não tem perfil, não vê nada.
        # --- FIM DA CORREÇÃO ---

        # REGRA 3: A regra para Membros e Secretárias continua a mesma
        if hasattr(user, 'perfil'):
            atendimentos_da_conta = Atendimento.objects.filter(conta__in=user.perfil.contas.all())
            return atendimentos_da_conta.filter(
                Q(responsavel=user) | Q(responsavel__isnull=True)
            ).order_by('-data_criacao')
        
        # REGRA 4: Se nenhuma das anteriores se aplicar, não mostra nada.
        return Atendimento.objects.none()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class AtendimentoDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Atendimento.objects.all()
    serializer_class = AtendimentoSerializer
    permission_classes = [permissions.IsAuthenticated, CanInteractWithAtendimento]


class RegistroVisitaListCreateView(generics.ListCreateAPIView):
    serializer_class = RegistroVisitaSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageCheckIn]

    def get_queryset(self):
        user = self.request.user
        queryset = RegistroVisita.objects.select_related(
            'municipe', 'conta_destino', 'registrado_por'
        )

        # --- LÓGICA DE PERMISSÃO COM INDENTAÇÃO CORRIGIDA ---
        if not user.is_superuser:
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta_destino__in=user.perfil.contas.all())
            else:
                queryset = RegistroVisita.objects.none()
        # --- FIM DA CORREÇÃO ---

        data_inicio_str = self.request.query_params.get('data_inicio', None)
        data_fim_str = self.request.query_params.get('data_fim', None)

        if data_inicio_str and data_fim_str:
            try:
                inicio_date = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
                fim_date = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
                inicio_datetime = timezone.make_aware(datetime.combine(inicio_date, time.min))
                fim_datetime = timezone.make_aware(datetime.combine(fim_date, time.max))
                queryset = queryset.filter(data_checkin__range=(inicio_datetime, fim_datetime))
            except (ValueError, TypeError):
                return RegistroVisita.objects.none()
        else:
            hoje_local = timezone.localtime()
            inicio_do_dia = hoje_local.replace(hour=0, minute=0, second=0, microsecond=0)
            fim_do_dia = hoje_local.replace(hour=23, minute=59, second=59, microsecond=999999)
            queryset = queryset.filter(data_checkin__range=(inicio_do_dia, fim_do_dia))
        
        return queryset.order_by('-data_checkin')

    def perform_create(self, serializer):
        serializer.save(registrado_por=self.request.user)

class RegistroVisitaDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    View para ver, atualizar e deletar um Registro de Visita específico.
    """
    queryset = RegistroVisita.objects.all()
    serializer_class = RegistroVisitaSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageCheckIn]


# -----------------------------------------------------------------------------
# Views de Solicitação de Agenda
# -----------------------------------------------------------------------------

class SolicitacaoAgendaListCreateView(generics.ListCreateAPIView):
    serializer_class = SolicitacaoAgendaSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageAgendas]

    def get_queryset(self):
        # A base da sua consulta continua a mesma
        queryset = SolicitacaoAgenda.objects.all()

        # Seus filtros de busca por data, conta e status continuam perfeitos
        data_inicio = self.request.query_params.get('data_inicio', None)
        data_fim = self.request.query_params.get('data_fim', None)
        conta_id = self.request.query_params.get('conta_id', None)
        status = self.request.query_params.get('status', None)

        if data_inicio and data_fim:
            queryset = queryset.filter(data_criacao__date__range=[data_inicio, data_fim])
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)
        if status:
            queryset = queryset.filter(status=status)

        # Sua lógica de permissão por usuário também continua perfeita
        user = self.request.user
        if not user.is_superuser:
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
            else:
                return SolicitacaoAgenda.objects.none()

        return queryset.order_by('-data_criacao')


class SolicitacaoAgendaDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = SolicitacaoAgenda.objects.all()
    serializer_class = SolicitacaoAgendaSerializer
    permission_classes = [IsAuthenticated, CanManageAgendas, CanAccessObjectByConta]


# -----------------------------------------------------------------------------
# Views de Usuários, Contas e Categorias
# -----------------------------------------------------------------------------

class UserListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    queryset = User.objects.filter(is_active=True).order_by('username')
    serializer_class = UserSerializer

class EspacoListCreateView(generics.ListCreateAPIView):
    """
    View para listar e criar Espaços.
    """
    queryset = Espaco.objects.filter(ativo=True).order_by('nome')
    serializer_class = EspacoSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Espaco.objects.filter(ativo=True).order_by('nome')
        
        if hasattr(user, 'perfil'):
            # Mostra apenas espaços vinculados às contas do usuário
            return Espaco.objects.filter(ativo=True, contas__in=user.perfil.contas.all()).distinct().order_by('nome')
            
        return Espaco.objects.none()

    def perform_create(self, serializer):
        # Ao criar um novo espaço, vincula-o automaticamente à primeira conta do usuário
        espaco = serializer.save()
        if hasattr(self.request.user, 'perfil'):
            primeira_conta = self.request.user.perfil.contas.first()
            if primeira_conta:
                espaco.contas.add(primeira_conta)

class EspacoDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    View para ver, editar e deletar um Espaço específico.
    """
    queryset = Espaco.objects.all()
    serializer_class = EspacoSerializer
    permission_classes = [permissions.IsAuthenticated, CanAccessEspaco]

class ContaListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    queryset = Conta.objects.all().order_by('nome')
    serializer_class = ContaSerializer


class CategoriaAtendimentoListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    queryset = CategoriaAtendimento.objects.filter(ativa=True)
    serializer_class = CategoriaAtendimentoSerializer

class CategoriaContatoListView(generics.ListAPIView):
    queryset = CategoriaContato.objects.filter(ativa=True)
    serializer_class = CategoriaContatoSerializer
    permission_classes = [IsAuthenticated]

# -----------------------------------------------------------------------------
# Views de Munícipe
# -----------------------------------------------------------------------------

class MunicipeListCreateView(generics.ListCreateAPIView):
    # A permissão de criar (POST) agora é livre para qualquer autenticado.
    # A permissão de ver (GET) será tratada no get_queryset e no frontend.
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MunicipeSerializer

    def get_queryset(self):
        user = self.request.user
        termo_busca = self.request.query_params.get('q', None)
        letra_inicial = self.request.query_params.get('letra', None)
        grupo_id = self.request.query_params.get('grupo', None)
        mostrar_duplicatas = self.request.query_params.get('duplicatas', None)

        base_queryset = Municipe.objects.prefetch_related('contas', 'categoria')

        # Se um grupo for solicitado, ele tem prioridade máxima.
        if grupo_id:
            return base_queryset.filter(grupo_duplicado=grupo_id).order_by('nome_completo')

        # --- LÓGICA DE PERMISSÃO DE VISUALIZAÇÃO REFINADA ---

        # Superusuário e Recepção veem TODOS os contatos.
        #if user.is_superuser or is_in_group(user, 'Recepção'):
        #    pass

        # Membro/Secretária continuam com a regra de contas.
        elif hasattr(user, 'perfil'):
            contas_usuario = user.perfil.contas.all()
            base_queryset = base_queryset.filter(contas__in=contas_usuario).distinct()
            
            # Se for da Recepção, aplica o filtro adicional de categoria
            if is_in_group(user, 'Recepção'):
                base_queryset = base_queryset.filter(categoria__nome='Munícipe')
        
        # Outros perfis (se existirem) não veem nada.
        else:
            return Municipe.objects.none()
        
        # Aplica os filtros de tela sobre a lista já permitida
        if mostrar_duplicatas == 'true':
            base_queryset = base_queryset.exclude(grupo_duplicado__isnull=True)
        
        if termo_busca:
            return base_queryset.filter(
                Q(nome_completo__icontains=termo_busca) |
                Q(nome_de_guerra__icontains=termo_busca) |
                Q(cpf__icontains=termo_busca) |
                Q(emails__contains=[{'email': termo_busca}]) |
                Q(cargo__icontains=termo_busca) |
                Q(orgao__icontains=termo_busca) |
                Q(categoria__nome__icontains=termo_busca)
            ).order_by('nome_completo')
        
        if letra_inicial:
            return base_queryset.filter(nome_completo__istartswith=letra_inicial).order_by('nome_completo')
        
        if mostrar_duplicatas == 'true':
            return base_queryset.order_by('grupo_duplicado', 'nome_completo')
        
        return base_queryset.order_by('-data_cadastro')[:100]

class MunicipeDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated, CanEditMunicipeDetails]
    queryset = Municipe.objects.all()
    serializer_class = MunicipeSerializer


class MunicipeDetailDataView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]
    serializer_class = MunicipeDetailSerializer
    queryset = Municipe.objects.all()


class MunicipeLookupView(generics.ListAPIView):
    serializer_class = MunicipeLookupSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Municipe.objects.all()

        if not user.is_superuser:
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(contas__in=user.perfil.contas.all())
            else:
                return Municipe.objects.none()

        termo_busca = self.request.query_params.get('q', None)

        if not termo_busca:
            return queryset.order_by('-data_cadastro')[:20]

        if termo_busca.isdigit():
            return queryset.filter(id=termo_busca)

        palavras = termo_busca.split()
        
        query_parts = [
            Q(nome_completo__icontains=palavra) | Q(nome_de_guerra__icontains=palavra)
            for palavra in palavras
        ]
        
        final_query = reduce(operator.and_, query_parts)
        
        resultados = queryset.filter(final_query)
            
        return resultados.order_by('nome_completo')[:20]


# -----------------------------------------------------------------------------
# Views de Tramitação e Anexos
# -----------------------------------------------------------------------------

class TramitacaoListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TramitacaoSerializer

    def get_queryset(self):
        return Tramitacao.objects.filter(atendimento__pk=self.kwargs['atendimento_pk'])

    def perform_create(self, serializer):
        atendimento_pk = self.kwargs['atendimento_pk']
        atendimento_instance = Atendimento.objects.get(pk=atendimento_pk)

        tramitacao = serializer.save(
            atendimento=atendimento_instance,
            usuario=self.request.user
        )

        notificar = self.request.data.get('notificar_municipe', False)

        municipe_email_principal = atendimento_instance.municipe.emails[0].get('email') if atendimento_instance.municipe and atendimento_instance.municipe.emails else None

        if notificar and municipe_email_principal:
            try:
                # 2. Busca os dados de personalização da Conta do atendimento
                conta = atendimento_instance.conta
                site_domain = Site.objects.get_current(self.request).domain
                protocol = self.request.scheme

                nome_instituicao = "Prefeitura Municipal"
                brasao_url = ''
                logo_conta_url = ''

                if conta:
                    nome_instituicao = conta.nome_instituicao or nome_instituicao
                    if conta.brasao_instituicao:
                        brasao_url = f"{protocol}://{site_domain}{conta.brasao_instituicao.url}"
                    if conta.logo_conta:
                        logo_conta_url = f"{protocol}://{site_domain}{conta.logo_conta.url}"

                # 3. Monta o contexto completo para o template
                context = {
                    'nome_municipe': atendimento_instance.municipe.nome_completo,
                    'protocolo': atendimento_instance.protocolo,
                    'titulo': atendimento_instance.titulo,
                    'despacho': tramitacao.despacho,
                    'nome_instituicao': nome_instituicao,
                    'brasao_url': brasao_url,
                    'logo_conta_url': logo_conta_url
                }
                html_message = render_to_string('emails/notificacao_tramitacao.html', context)
                plain_message = f"Houve um novo andamento no seu atendimento ({atendimento_instance.protocolo}): {tramitacao.despacho}"

                send_mail(
                    f"Atualização do seu Atendimento - Protocolo: {atendimento_instance.protocolo}",
                    plain_message,
                    settings.DEFAULT_FROM_EMAIL,
                    [municipe_email_principal],
                    html_message=html_message
                )
            except Exception as e:
                print(f"ERRO ao enviar e-mail de andamento: {e}")


class TramitacaoDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Tramitacao.objects.all()
    serializer_class = TramitacaoSerializer
    permission_classes = [permissions.IsAuthenticated]


class AnexoListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AnexoSerializer
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        return Anexo.objects.filter(atendimento__pk=self.kwargs['atendimento_pk'])

    def perform_create(self, serializer):
        serializer.save(
            atendimento=Atendimento.objects.get(pk=self.kwargs['atendimento_pk']),
            usuario=self.request.user
        )


# -----------------------------------------------------------------------------
# Views de Autenticação e Senha
# -----------------------------------------------------------------------------

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Adiciona dados customizados ao token
        token['username'] = user.username
        token['is_superuser'] = user.is_superuser
        token['groups'] = list(user.groups.values_list('name', flat=True))
        token['user_permissions'] = list(user.get_all_permissions())

        if hasattr(user, 'perfil'):
            token['perfil'] = {
                "id": user.perfil.id,
                "contas": list(user.perfil.contas.all().values_list('id', flat=True))
            }
        
        return token

class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer


class CustomPasswordResetView(APIView):
    permission_classes = []

    def post(self, request, *args, **kwargs):
        email = request.data.get('email')
        if not email:
            return Response({'error': 'Email é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)

        associated_users = User.objects.filter(email__iexact=email)
        if associated_users.exists():
            for user in associated_users:
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                context = {'uid': uid, 'token': token, 'user': user}
                html_message = render_to_string('registration/password_reset_email.html', context)
                subject = render_to_string('registration/password_reset_subject.txt', context)
                send_mail(
                    subject.strip(),
                    "Link para redefinição de senha.",
                    'comunicacao.gabinete@mogidascruzes.sp.gov.br',
                    [user.email],
                    html_message=html_message
                )
        return Response({'status': 'success'}, status=status.HTTP_200_OK)


class CustomPasswordResetConfirmView(APIView):
    permission_classes = []

    def post(self, request, *args, **kwargs):
        uid = request.data.get('uid')
        token = request.data.get('token')
        new_password1 = request.data.get('new_password1')
        new_password2 = request.data.get('new_password2')

        try:
            uid_decoded = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=uid_decoded)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None

        if user is not None and default_token_generator.check_token(user, token):

            # --- AQUI ESTÁ A CORREÇÃO ---
            # Criamos um dicionário apenas com os dados que o formulário espera
            form_data = {'new_password1': new_password1, 'new_password2': new_password2}
            form = SetPasswordForm(user, form_data)
            # --- FIM DA CORREÇÃO ---

            if form.is_valid():
                form.save() # Salva a nova senha
                return Response({'status': 'success', 'message': 'Senha redefinida com sucesso.'}, status=status.HTTP_200_OK)
            else:
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({'error': 'Link de redefinição inválido ou já foi usado.'}, status=status.HTTP_400_BAD_REQUEST)


# -----------------------------------------------------------------------------
# Views de Relatórios e Gráficos (Dashboard)
# -----------------------------------------------------------------------------

class RelatorioAtendimentosPorStatusView(APIView):
    permission_classes = [IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        user = self.request.user
        queryset = Atendimento.objects.all()

        # Lógica de permissão UNIFICADA
        if not user.is_superuser:
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
            else:
                queryset = Atendimento.objects.none()

        data_inicio = request.query_params.get('data_inicio', None)
        data_fim = request.query_params.get('data_fim', None)

        if data_inicio:
            queryset = queryset.filter(data_criacao__gte=f'{data_inicio} 00:00:00')
        if data_fim:
            queryset = queryset.filter(data_criacao__lte=f'{data_fim} 23:59:59')

        data = queryset.values('status').annotate(total=Count('status')).order_by('status')
        return Response(data)


class RelatorioAtendimentosPorContaView(APIView):
    permission_classes = [IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        user = self.request.user
        queryset = Atendimento.objects.all()

        # Lógica de permissão UNIFICADA
        if not user.is_superuser:
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
            else:
                queryset = Atendimento.objects.none()

        data_inicio = request.query_params.get('data_inicio', None)
        data_fim = request.query_params.get('data_fim', None)

        if data_inicio:
            queryset = queryset.filter(data_criacao__gte=f'{data_inicio} 00:00:00')
        if data_fim:
            queryset = queryset.filter(data_criacao__lte=f'{data_fim} 23:59:59')

        data = queryset.values('conta__nome').annotate(total=Count('id')).order_by('-total')
        return Response(data)


class RelatorioAtendimentosPorCategoriaView(APIView):
    permission_classes = [IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        user = self.request.user
        queryset = Atendimento.objects.all()

        # Lógica de permissão UNIFICADA
        if not user.is_superuser:
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
            else:
                queryset = Atendimento.objects.none()

        data_inicio = request.query_params.get('data_inicio', None)
        data_fim = request.query_params.get('data_fim', None)

        if data_inicio:
            queryset = queryset.filter(data_criacao__gte=f'{data_inicio} 00:00:00')
        if data_fim:
            queryset = queryset.filter(data_criacao__lte=f'{data_fim} 23:59:59')

        data = queryset.values('categorias__nome').annotate(total=Count('id')).order_by('-total')
        return Response(data)


class DashboardSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        data = {}

        agora_local = timezone.localtime()
        inicio_do_dia = agora_local.replace(hour=0, minute=0, second=0, microsecond=0)
        fim_do_dia = agora_local.replace(hour=23, minute=59, second=59, microsecond=999999)

        if is_in_group(user, 'Recepção'):
            data['triagens_do_dia'] = Atendimento.objects.filter(
                created_by=user, 
                data_criacao__range=(inicio_do_dia, fim_do_dia)
            ).count()
            data['checkins_do_dia'] = RegistroVisita.objects.filter(
                registrado_por=user,
                data_checkin__range=(inicio_do_dia, fim_do_dia)
            ).count()

        if hasattr(user, 'perfil') and (is_in_group(user, 'Membro do Gabinete') or is_in_group(user, 'Secretária')):
            atendimentos_do_usuario = Atendimento.objects.filter(conta__in=user.perfil.contas.all())

            data['novos_atendimentos'] = atendimentos_do_usuario.filter(responsavel=user, status='ABERTO').count()
            data['atendimentos_em_aberto'] = atendimentos_do_usuario.filter(status='ABERTO').count()
            data['atendimentos_em_analise'] = atendimentos_do_usuario.filter(
                Q(status='EM_ANALISE'),
                Q(responsavel=user) | Q(responsavel__isnull=True)
            ).count()

        if hasattr(user, 'perfil') and is_in_group(user, 'Secretária'):
            agendas_da_secretaria = SolicitacaoAgenda.objects.filter(conta__in=user.perfil.contas.all())
            data['agendas_em_aberto'] = agendas_da_secretaria.filter(status='SOLICITADO').count()
            data['agendas_em_analise'] = agendas_da_secretaria.filter(status='EM_ANALISE').count()

        return Response(data)


# -----------------------------------------------------------------------------
# Views de Geração de Documentos (PDF, Excel)
# -----------------------------------------------------------------------------

class GerarPdfAtendimentosView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        user = request.user
        queryset = Atendimento.objects.all()

        if not (user.is_superuser or is_in_group(user, 'Recepção')):
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
                queryset = queryset.filter(Q(responsavel=user) | Q(responsavel__isnull=True))
            else:
                queryset = Atendimento.objects.none()

        status = request.query_params.get('status', None)
        conta_id = request.query_params.get('conta_id', None)
        data_inicio = request.query_params.get('data_inicio', None)
        data_fim = request.query_params.get('data_fim', None)

        if status:
            queryset = queryset.filter(status=status)
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)
        if data_inicio:
            queryset = queryset.filter(data_criacao__date__gte=data_inicio)
        if data_fim:
            queryset = queryset.filter(data_criacao__date__lte=data_fim)

        conta_id = request.query_params.get('conta_id', None)
        conta_contexto = None
        
        if conta_id:
            conta_contexto = Conta.objects.filter(id=conta_id).first()
        elif not request.user.is_superuser and hasattr(request.user, 'perfil'):
            conta_contexto = request.user.perfil.contas.first()
        
        # Prepara as informações de personalização
        nome_instituicao = "Prefeitura Municipal" # Valor padrão
        brasao_url = ''
        logo_conta_url = ''

        if conta_contexto:
            nome_instituicao = conta_contexto.nome_instituicao or nome_instituicao
            if conta_contexto.brasao_instituicao:
                brasao_url = request.build_absolute_uri(conta_contexto.brasao_instituicao.url)
            if conta_contexto.logo_conta:
                logo_conta_url = request.build_absolute_uri(conta_contexto.logo_conta.url)

        context = {
            'atendimentos': queryset.select_related('municipe', 'conta', 'responsavel').prefetch_related('categorias'),
            'nome_instituicao': nome_instituicao,
            'brasao_url': brasao_url,
            'logo_conta_url': logo_conta_url,
            'logo_siga_url': request.build_absolute_uri('/static/images/logo-siga-gab.png'),
        }
        html_string = render_to_string('atendimentos/relatorio_atendimentos.html', context)
        pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()

        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="relatorio_atendimentos.pdf"'
        return response


class GerarPdfAtendimentoDetailView(APIView):
    permission_classes = [IsAuthenticated, CanInteractWithAtendimento]

    def get(self, request, pk, *args, **kwargs):
        try:
            atendimento = Atendimento.objects.select_related(
                'municipe', 'conta', 'responsavel'
            ).prefetch_related(
                'tramitacoes__usuario', 'anexos__usuario', 'categorias'
            ).get(pk=pk)
            self.check_object_permissions(self.request, atendimento)
        except Atendimento.DoesNotExist:
            return Response({'detail': 'Atendimento não encontrado.'}, status=status.HTTP_404_NOT_FOUND)
        
        try:
            conta_contexto = atendimento.conta
            
            nome_instituicao = "Prefeitura Municipal" # Valor padrão
            brasao_url = ''
            logo_conta_url = ''

            if conta_contexto:
                nome_instituicao = conta_contexto.nome_instituicao or nome_instituicao
                if conta_contexto.brasao_instituicao:
                    brasao_url = request.build_absolute_uri(conta_contexto.brasao_instituicao.url)
                if conta_contexto.logo_conta:
                    logo_conta_url = request.build_absolute_uri(conta_contexto.logo_conta.url)

            context = {
                'atendimento': atendimento,
                'nome_instituicao': nome_instituicao,
                'brasao_url': brasao_url,
                'logo_conta_url': logo_conta_url,
                'logo_siga_url': request.build_absolute_uri('/static/images/logo-siga-gab.png'),
            }
            html_string = render_to_string('atendimentos/relatorio_atendimento_detalhe.html', context)
            pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()

            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="atendimento_{atendimento.protocolo}.pdf"'
            return response
        except Exception as e:
            print(f"ERRO INESPERADO AO GERAR PDF: {e}")
            return Response({'detail': f'Ocorreu um erro interno ao gerar o PDF: {e}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GerarPdfAgendasReportView(APIView):
    permission_classes = [IsAuthenticated, CanViewAgendaReports]

    def get(self, request, *args, **kwargs):
        user = request.user
        queryset = SolicitacaoAgenda.objects.select_related('solicitante', 'conta').order_by('data_criacao')

        if not user.is_superuser:
            if hasattr(user, 'perfil'):
                # Mostra apenas solicitações das contas vinculadas ao usuário
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
            else:
                # Se não for superusuário e não tiver perfil, não vê nada.
                queryset = SolicitacaoAgenda.objects.none()

        data_inicio = request.query_params.get('data_inicio')
        data_fim = request.query_params.get('data_fim')
        conta_id = request.query_params.get('conta_id')
        status_param = request.query_params.get('status')

        if data_inicio and data_fim:
            queryset = queryset.filter(data_criacao__range=[data_inicio, data_fim])
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)
        if status_param:
            queryset = queryset.filter(status=status_param)

        conta_id = request.query_params.get('conta_id', None)
        conta_contexto = None
        
        if conta_id:
            conta_contexto = Conta.objects.filter(id=conta_id).first()
        elif not request.user.is_superuser and hasattr(request.user, 'perfil'):
            conta_contexto = request.user.perfil.contas.first()
        
        # Prepara as informações de personalização
        nome_instituicao = "Prefeitura Municipal" # Valor padrão
        brasao_url = ''
        logo_conta_url = ''

        if conta_contexto:
            nome_instituicao = conta_contexto.nome_instituicao or nome_instituicao
            if conta_contexto.brasao_instituicao:
                brasao_url = request.build_absolute_uri(conta_contexto.brasao_instituicao.url)
            if conta_contexto.logo_conta:
                logo_conta_url = request.build_absolute_uri(conta_contexto.logo_conta.url)

        context = {
            'solicitacoes': queryset,
            'data_emissao': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
            'usuario_emissao': request.user.get_full_name() or request.user.username,
            'nome_instituicao': nome_instituicao,
            'brasao_url': brasao_url,
            'logo_conta_url': logo_conta_url,
            'logo_siga_url': request.build_absolute_uri('/static/images/logo-siga-gab.png'),
        }

        try:
            html_string = render_to_string('agendas/relatorio_agendas.html', context)
            pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()
            
            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="relatorio_agendas_{datetime.now().strftime("%Y%m%d")}.pdf"'
            return response
        except Exception as e:
            print(f"ERRO INESPERADO AO GERAR PDF DE AGENDAS: {e}")
            return Response(
                {'detail': f'Ocorreu um erro interno ao gerar o PDF: {e}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class ExportMunicipesExcelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        
        # 1. A busca inicial agora usa prefetch_related para otimizar a busca das múltiplas contas
        queryset = Municipe.objects.prefetch_related('categoria', 'contas').all()

        # 2. A lógica de filtragem da lista de contatos é aplicada primeiro
        #    (espelhando a lógica da sua tela de Contatos)
        if is_in_group(user, 'Membro do Gabinete') or is_in_group(user, 'Secretária'):
            if hasattr(user, 'perfil'):
                contas_usuario = user.perfil.contas.all()
                queryset = queryset.filter(
                    Q(contas__isnull=True) | Q(contas__in=contas_usuario)
                ).distinct()
            else:
                queryset = queryset.filter(contas__isnull=True)

        # 3. O filtro de busca por texto é aplicado sobre a lista já filtrada
        termo_busca = self.request.query_params.get('q', None)
        if termo_busca:
            queryset = queryset.filter(
                Q(nome_completo__icontains=termo_busca) |
                Q(cpf__icontains=termo_busca) |
                Q(email__icontains=termo_busca) |
                Q(cargo__icontains=termo_busca) |
                Q(orgao__icontains=termo_busca) |
                Q(categoria__nome__icontains=termo_busca)
            )

        # --- PREPARAÇÃO DO ARQUIVO EXCEL ---
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Contatos'

        # Adicionamos a coluna "Contas Vinculadas"
        headers = ['Nome Completo', 'CPF', 'Data de Nascimento', 'Email', 'Telefone Principal', 'Cargo', 'Órgão', 'Categoria', 'Contas Vinculadas']
        sheet.append(headers)

        # --- LOOP PARA PREENCHER AS LINHAS ---
        for municipe in queryset:
            # Formata o telefone principal
            telefone = municipe.telefones[0].get('numero', '') if municipe.telefones else ''
            
            # Formata a data de nascimento
            data_nasc_formatada = municipe.data_nascimento.strftime('%d/%m/%Y') if municipe.data_nascimento else ''
            
            # Formata a categoria
            categoria_nome = municipe.categoria.nome if municipe.categoria else ''

            # --- A MÁGICA PARA AS MÚLTIPLAS CONTAS ---
            # Pega o nome de cada conta e os une em uma única string, separados por vírgula
            contas_vinculadas = ", ".join([conta.nome for conta in municipe.contas.all()])

            # Adiciona a linha completa à planilha
            sheet.append([
                municipe.nome_completo,
                municipe.cpf,
                data_nasc_formatada,
                municipe.email,
                telefone,
                municipe.cargo,
                municipe.orgao,
                categoria_nome,
                contas_vinculadas # <<< Usa a string formatada
            ])

        # --- GERA A RESPOSTA HTTP COM O ARQUIVO ---
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="contatos_{datetime.now().strftime("%Y%m%d")}.xlsx"'
        workbook.save(response)

        return response

class GerarPdfGoogleAgendaView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        # Envolva TODO o código da função em um bloco try...except
        try:
            # --------------------------------------------------------------------
            # A PARTIR DAQUI, TODO O SEU CÓDIGO ORIGINAL DA FUNÇÃO get VEM AQUI,
            # COM UMA INDENTAÇÃO ADICIONAL.
            # --------------------------------------------------------------------
            
            user = request.user
            
            # --- ETAPA 1: BUSCAR CREDENCIAIS ---
            try:
                token_google = GoogleApiToken.objects.get(usuario=user)
            except GoogleApiToken.DoesNotExist:
                return Response({'detail': 'Autorização do Google não encontrada.'}, status=status.HTTP_400_BAD_REQUEST)

            credentials = Credentials(
                token=token_google.access_token,
                refresh_token=token_google.refresh_token,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=settings.GOOGLE_CLIENT_ID,
                client_secret=settings.GOOGLE_CLIENT_SECRET
            )
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                token_google.access_token = credentials.token
                token_google.save()

            # --- ETAPA 2: BUSCAR EVENTOS ---
            service = build('calendar', 'v3', credentials=credentials)
            
            start_date_str = request.query_params.get('data_inicio')
            end_date_str = request.query_params.get('data_fim')
            
            start_date = parse_datetime(start_date_str)
            end_date = parse_datetime(end_date_str) + timedelta(days=1, seconds=-1)
            
            events_result = service.events().list(
                calendarId='primary', 
                timeMin=start_date.isoformat() + "Z", # Adicione "Z" para UTC
                timeMax=end_date.isoformat() + "Z",   # Adicione "Z" para UTC
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            # --- ETAPA 3: RENDERIZAR O PDF ---
            eventos_por_dia = defaultdict(list)
            for event in events:
                if 'dateTime' not in event['start']:
                    continue
                start_str = event['start'].get('dateTime')
                start_obj = parse_datetime(start_str)
                dia = start_obj.date()
                if 'dateTime' in event['start']: event['start']['dateTime'] = start_obj
                if 'date' in event['start']: event['start']['date'] = start_obj.date()
                eventos_por_dia[dia].append(event)
            
            meses_do_relatorio = []
            data_corrente = parse_datetime(start_date_str).date()
            data_final_loop = parse_datetime(end_date_str).date()

            while data_corrente <= data_final_loop:
                mes_ano_atual = (data_corrente.year, data_corrente.month)
                cal = calendar.Calendar()
                semanas_do_mes = cal.monthdatescalendar(data_corrente.year, data_corrente.month)
                semanas_com_eventos = []
                for semana in semanas_do_mes:
                    dias_da_semana_com_eventos = []
                    for dia in semana:
                        dias_da_semana_com_eventos.append({
                            'data': dia,
                            'eventos': eventos_por_dia.get(dia, [])
                        })
                    semanas_com_eventos.append(dias_da_semana_com_eventos)
                
                nomes_dos_meses = [
                    'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
                    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'
                ]
                # Pega o nome do mês em português usando o número do mês como índice
                nome_mes_pt = nomes_dos_meses[data_corrente.month - 1]

                meses_do_relatorio.append({
                    'nome_mes': f"{nome_mes_pt} de {data_corrente.year}", # Usa o nome traduzido
                    'mes_numero': data_corrente.month,
                    'semanas': semanas_com_eventos
                })
                
                proximo_mes = (data_corrente.replace(day=28) + timedelta(days=4)).replace(day=1)
                if (proximo_mes.year, proximo_mes.month) == mes_ano_atual: break
                data_corrente = proximo_mes

            logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-brasao-prefeitura.png')
            logo_data = ""
            try:
                with open(logo_path, "rb") as image_file:
                    logo_data = base64.b64encode(image_file.read()).decode('utf-8')
            except FileNotFoundError:
                print(f"ARQUIVO DE LOGO NÃO ENCONTRADO EM: {logo_path}")

            context = { 
                'hoje': timezone.now().date(),
                'meses_do_relatorio': meses_do_relatorio, 
                'logo_gestao_url': f"data:image/png;base64,{logo_data}", 
            }
            html_string = render_to_string('agendas/relatorio_google_agenda.html', context)
            pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()
            
            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="relatorio_google_agenda.pdf"'
            return response

        except Exception as e:
            # Se QUALQUER erro acontecer no bloco 'try', este código será executado.
            tb_string = traceback.format_exc()
            
            # Ele retorna o traceback completo como uma resposta de texto simples.
            return HttpResponse(
                f"Ocorreu um erro interno no servidor:\n\n{tb_string}",
                status=500,
                content_type="text/plain; charset=utf-8"
            )

class GerarPdfCheckinsView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanManageCheckIn]

    def get(self, request, *args, **kwargs):
        # Começa com todos os registros, otimizando com select_related
        queryset = RegistroVisita.objects.select_related(
            'municipe', 'conta_destino', 'registrado_por'
        ).all()

        # Pega os filtros de data da URL
        data_inicio_str = self.request.query_params.get('data_inicio', None)
        data_fim_str = self.request.query_params.get('data_fim', None)

        # Aplica o filtro de data, se fornecido
        if data_inicio_str and data_fim_str:
            try:
                inicio_date = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
                fim_date = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
                inicio_datetime = timezone.make_aware(datetime.combine(inicio_date, time.min))
                fim_datetime = timezone.make_aware(datetime.combine(fim_date, time.max))
                queryset = queryset.filter(data_checkin__range=(inicio_datetime, fim_datetime))
            except (ValueError, TypeError):
                # Se as datas forem inválidas, retorna uma lista vazia
                queryset = RegistroVisita.objects.none()

        conta_id = request.query_params.get('conta_id', None)
        conta_contexto = None
        
        if conta_id:
            conta_contexto = Conta.objects.filter(id=conta_id).first()
        elif not request.user.is_superuser and hasattr(request.user, 'perfil'):
            conta_contexto = request.user.perfil.contas.first()
        
        # Prepara as informações de personalização
        nome_instituicao = "Prefeitura Municipal" # Valor padrão
        brasao_url = ''
        logo_conta_url = ''

        if conta_contexto:
            nome_instituicao = conta_contexto.nome_instituicao or nome_instituicao
            if conta_contexto.brasao_instituicao:
                brasao_url = request.build_absolute_uri(conta_contexto.brasao_instituicao.url)
            if conta_contexto.logo_conta:
                logo_conta_url = request.build_absolute_uri(conta_contexto.logo_conta.url)

        # Prepara o contexto para o template
        context = {
            'visitas': queryset,
            'nome_instituicao': nome_instituicao,
            'brasao_url': brasao_url,
            'logo_conta_url': logo_conta_url,
            'logo_siga_url': request.build_absolute_uri('/static/images/logo-siga-gab.png'),
        }

        # Renderiza o HTML e converte para PDF
        html_string = render_to_string('relatorios/relatorio_checkins.html', context)
        pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()

        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="relatorio_checkins_{datetime.now().strftime("%Y%m%d")}.pdf"'
        return response


# -----------------------------------------------------------------------------
# Views de Notificação e Busca
# -----------------------------------------------------------------------------

class NotificacaoListView(generics.ListAPIView):
    serializer_class = NotificacaoSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notificacao.objects.filter(usuario=self.request.user, lida=False)


class MarcarNotificacaoComoLidaView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            notificacao = Notificacao.objects.get(pk=pk, usuario=request.user)
            notificacao.lida = True
            notificacao.save()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Notificacao.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)


class AniversariantesDoDiaView(generics.ListAPIView):
    serializer_class = MunicipeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        
        # --- AQUI ESTÁ A NOVA LÓGICA DE PERMISSÃO ---
        
        # 1. Começa com uma base de contatos permitidos para o usuário
        if user.is_superuser:
            base_queryset = Municipe.objects.all()
        elif hasattr(user, 'perfil'):
            # Filtra apenas os contatos das contas vinculadas ao usuário
            base_queryset = Municipe.objects.filter(contas__in=user.perfil.contas.all()).distinct()
        else:
            # Se não for superusuário e não tiver perfil, não vê nenhum contato
            return Municipe.objects.none()

        # 2. Agora, filtra os aniversariantes APENAS da lista de contatos permitidos
        hoje = timezone.now()
        return base_queryset.filter(
            data_nascimento__day=hoje.day,
            data_nascimento__month=hoje.month
        )
        # --- FIM DA CORREÇÃO ---


class BuscaGlobalView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        termo_busca = self.request.query_params.get('q', None)
        if not termo_busca or len(termo_busca) < 3:
            return Response([])

        user = self.request.user
        resultados = []

        # Busca de Atendimentos (sem alteração)
        if not is_in_group(user, 'Recepção'):
            atendimento_qs = Atendimento.objects.all()
            if not user.is_superuser:
                if hasattr(user, 'perfil'):
                    atendimento_qs = atendimento_qs.filter(conta__in=user.perfil.contas.all())
                    atendimento_qs = atendimento_qs.filter(Q(responsavel=user) | Q(responsavel__isnull=True))
                else:
                    atendimento_qs = Atendimento.objects.none()
            
            atendimentos_encontrados = atendimento_qs.filter(
                Q(titulo__icontains=termo_busca) | Q(protocolo__icontains=termo_busca)
            )[:5]

            for atendimento in atendimentos_encontrados:
                resultados.append({
                    'tipo': 'atendimento', 'id': atendimento.id,
                    'texto_principal': f"Protocolo {atendimento.protocolo}",
                    'texto_secundario': atendimento.titulo,
                    'url': f"/atendimentos/{atendimento.id}"
                })

        # Lógica de permissão para Munícipes (sem alteração)
        if user.is_superuser:
            municipe_qs = Municipe.objects.all()
        # REGRA NOVA: Recepção busca APENAS contatos da categoria 'Munícipe'.
        elif hasattr(user, 'perfil'):
            contas_usuario = user.perfil.contas.all()
            municipe_qs = Municipe.objects.filter(contas__in=contas_usuario).distinct()
            
            # Se for da Recepção, aplica o filtro adicional de categoria
            if is_in_group(user, 'Recepção'):
                municipe_qs = municipe_qs.filter(categoria__nome='Munícipe')
        elif hasattr(user, 'perfil'):
            contas_usuario = user.perfil.contas.all()
            municipe_qs = Municipe.objects.filter(
                Q(contas__isnull=True) | Q(contas__in=contas_usuario)
            ).distinct()
        else:
            municipe_qs = Municipe.objects.none()

        # --- AQUI ESTÁ A CORREÇÃO DA BUSCA POR MUNÍCIPES ---
        query_palavras = Q()
        for palavra in termo_busca.split():
            # Agora, cada palavra é buscada no nome completo OU no nome de guerra
            query_palavras &= (Q(nome_completo__icontains=palavra) | Q(nome_de_guerra__icontains=palavra))
        
        # A busca por CPF continua separada
        query_cpf = Q(cpf__icontains=termo_busca)
        
        # A consulta final une as duas buscas com um "OU"
        municipes_encontrados = municipe_qs.filter(query_palavras | query_cpf).distinct()[:100]
        # --- FIM DA CORREÇÃO ---

        for municipe in municipes_encontrados:
            resultados.append({
                'tipo': 'municipe', 'id': municipe.id,
                'texto_principal': municipe.nome_completo,
                'texto_secundario': f"CPF: {municipe.cpf or 'Não informado'}",
                'url': f"/municipes/{municipe.id}/historico"
            })

        serializer = BuscaGlobalSerializer(resultados, many=True)
        return Response(serializer.data)


# -----------------------------------------------------------------------------
# Views de Integração com Google API
# -----------------------------------------------------------------------------

class GoogleAuthInitiateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        scopes = ['https://www.googleapis.com/auth/calendar.events']
        redirect_uri = 'https://gabinete.mogidascruzes.sp.gov.br/api/google/auth/callback/'

        client_config = {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }

        flow = Flow.from_client_config(client_config, scopes=scopes, redirect_uri=redirect_uri)
        authorization_url, state = flow.authorization_url(access_type='offline', prompt='consent')
        
        request.session['google_oauth2_state'] = state
        request.session['google_auth_user_id'] = request.user.id

        return Response({'authorization_url': authorization_url})


class GoogleAuthCallbackView(APIView):
    def get(self, request, *args, **kwargs):
        state = request.query_params.get('state')
        session_state = request.session.get('google_oauth2_state')
        if not state or state != session_state:
            return Response({'error': 'State mismatch.'}, status=status.HTTP_400_BAD_REQUEST)
        
        user_id = request.session.get('google_auth_user_id')
        if not user_id:
            return Response({'error': 'Sessão de usuário não encontrada.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({'error': 'Usuário da sessão inválido.'}, status=status.HTTP_400_BAD_REQUEST)

        redirect_uri = 'https://gabinete.mogidascruzes.sp.gov.br/api/google/auth/callback/'
        scopes = ['https://www.googleapis.com/auth/calendar.events']
        client_config = {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = Flow.from_client_config(client_config, scopes=scopes, redirect_uri=redirect_uri)
        flow.fetch_token(authorization_response=request.build_absolute_uri())
        credentials = flow.credentials

        GoogleApiToken.objects.update_or_create(
            usuario=user,
            defaults={
                'access_token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'expires_at': credentials.expiry
            }
        )
        return Response({'status': 'success', 'message': 'Autorização concluída com sucesso!'})

class CriarEventoGoogleView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanCreateGoogleEvent]

    def post(self, request, pk, *args, **kwargs):
        user = request.user
        
        # 1. Busca a solicitação de agenda e o token do Google do usuário
        try:
            solicitacao = SolicitacaoAgenda.objects.get(pk=pk)
            token_google = GoogleApiToken.objects.get(usuario=user)
        except SolicitacaoAgenda.DoesNotExist:
            return Response({'detail': 'Solicitação de agenda não encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        except GoogleApiToken.DoesNotExist:
            return Response({'detail': 'Autorização do Google não encontrada. Por favor, autorize o acesso nas configurações.'}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Garante que a solicitação está no status correto para ser agendada
        if solicitacao.status != 'AGENDADO' or not solicitacao.data_agendada or not solicitacao.data_agendada_fim:
            return Response({'detail': 'Esta solicitação não está confirmada ou não possui data/hora definidas.'}, status=status.HTTP_400_BAD_REQUEST)

        # 3. Monta as credenciais do Google a partir do token salvo
        credentials = Credentials(
            token=token_google.access_token,
            refresh_token=token_google.refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            scopes=['https://www.googleapis.com/auth/calendar.events']
        )

        # 4. Verifica se o token de acesso expirou e o renova se necessário
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            # Salva o novo token de acesso atualizado no banco
            token_google.access_token = credentials.token
            token_google.save()

        # 5. Monta o evento a ser criado
        # Regra da Descrição: Assunto + Detalhes
        descricao_formatada = (
            f"Assunto: {solicitacao.assunto}\n\n"
            f"Detalhes Adicionais:\n{solicitacao.detalhes or 'Não foram fornecidos.'}"
        )

        # Regra do Local: Nome do espaço ou o padrão "Gabinete da Prefeita"
        local_evento = "Gabinete da Prefeita"
        if solicitacao.espaco and solicitacao.espaco.nome:
            local_evento = solicitacao.espaco.nome

        # Monta o dicionário final do evento para a API do Google
        evento = {
            'summary': solicitacao.solicitante.nome_completo, # Regra do Título: Nome do solicitante
            'location': local_evento, # Regra do Local
            'description': descricao_formatada, # Regra da Descrição
            'start': {
                'dateTime': solicitacao.data_agendada.isoformat(),
                'timeZone': 'America/Sao_Paulo',
            },
            'end': {
                'dateTime': solicitacao.data_agendada_fim.isoformat(),
                'timeZone': 'America/Sao_Paulo',
            },
        }

        # 6. Tenta criar o evento usando a API do Google
        try:
            service = build('calendar', 'v3', credentials=credentials)
            evento_criado = service.events().insert(calendarId='primary', body=evento).execute()
            
            # Adiciona o link do evento do Google à nossa solicitação (opcional, mas muito útil)
            solicitacao.link_google_agenda = evento_criado.get('htmlLink')
            solicitacao.save()
            
            return Response({
                'status': 'success',
                'detail': 'Evento criado com sucesso no Google Agenda!',
                'googleEventUrl': evento_criado.get('htmlLink')
            }, status=status.HTTP_201_CREATED)

        except HttpError as error:
            print(f"Ocorreu um erro na API do Google: {error}")
            return Response({'detail': f'Falha ao criar evento no Google Agenda: {error}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
class ListarEventosGoogleView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanCreateGoogleEvent]

    def get(self, request, *args, **kwargs):
        # --- INÍCIO DO BLOCO DE CAPTURA DE ERRO ---
        try:
            # Todo o código original da função fica dentro do 'try'
            user = request.user
            try:
                token_google = GoogleApiToken.objects.get(usuario=user)
            except GoogleApiToken.DoesNotExist:
                return Response({'detail': 'Autorização do Google não encontrada.'}, status=status.HTTP_400_BAD_REQUEST)

            credentials = Credentials(
                token=token_google.access_token,
                refresh_token=token_google.refresh_token,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=settings.GOOGLE_CLIENT_ID,
                client_secret=settings.GOOGLE_CLIENT_SECRET
            )

            if credentials.expired and credentials.refresh_token:
                credentials.refresh(GoogleAuthRequest())
                token_google.access_token = credentials.token
                token_google.save()

            service = build('calendar', 'v3', credentials=credentials)
            
            now = timezone.now()
            start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_period = start_of_month + timezone.timedelta(days=90)

            events_result = service.events().list(
                calendarId='primary', 
                timeMin=start_of_month.isoformat(),
                timeMax=end_period.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            eventos_formatados = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                end = event['end'].get('dateTime', event['end'].get('date'))
                eventos_formatados.append({
                    'id': event['id'],
                    'title': event.get('summary', 'Sem Título'),
                    'start': start,
                    'end': end,
                    'color': '#3788D8',
                    'allDay': 'date' in event['start'],
                    'description': event.get('description', 'Sem descrição.'),
                    'location': event.get('location', ''),
                    'htmlLink': event.get('htmlLink', '')
                })

            return Response(eventos_formatados)

        except Exception as e:
            # Se QUALQUER erro acontecer, este bloco será executado
            tb_string = traceback.format_exc()
            
            # Ele retorna o traceback completo como uma resposta de texto
            return HttpResponse(
                f"Ocorreu um erro interno no servidor (DEBUG):\n\n{tb_string}",
                status=500,
                content_type="text/plain; charset=utf-8"
            )
        # --- FIM DO BLOCO DE CAPTURA DE ERRO ---

class AdicionarEventoGoogleView(APIView):
    """
    Cria um novo evento no Google Agenda do usuário.
    """
    permission_classes = [permissions.IsAuthenticated, CanCreateGoogleEvent]

    def post(self, request, *args, **kwargs):
        user = request.user
        try:
            token_google = GoogleApiToken.objects.get(usuario=user)
        except GoogleApiToken.DoesNotExist:
            return Response({'detail': 'Autorização do Google não encontrada.'}, status=status.HTTP_400_BAD_REQUEST)

        credentials = Credentials(
            token=token_google.access_token,
            refresh_token=token_google.refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET
        )

        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_google.access_token = credentials.token
            token_google.save()

        # Pega os dados do evento enviados pelo frontend
        evento_data = request.data
        evento = {
            'summary': evento_data.get('title'),
            'description': evento_data.get('description'),
            'start': {'dateTime': evento_data.get('start'), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': evento_data.get('end'), 'timeZone': 'America/Sao_Paulo'},
        }

        if evento_data.get('location'):
            evento['location'] = evento_data.get('location')

        try:
            service = build('calendar', 'v3', credentials=credentials)
            evento_criado = service.events().insert(calendarId='primary', body=evento).execute()
            return Response({'status': 'success', 'detail': 'Evento criado com sucesso!'}, status=status.HTTP_201_CREATED)
        except HttpError as error:
            return Response({'detail': f'Falha ao criar evento no Google Agenda: {error}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class EditarExcluirEventoGoogleView(APIView):
    """
    Edita (PATCH) ou Exclui (DELETE) um evento existente no Google Agenda.
    """
    permission_classes = [permissions.IsAuthenticated, CanCreateGoogleEvent]

    def patch(self, request, eventId, *args, **kwargs):
        user = request.user
        try:
            token_google = GoogleApiToken.objects.get(usuario=user)
        except GoogleApiToken.DoesNotExist:
            return Response({'detail': 'Autorização do Google não encontrada.'}, status=status.HTTP_400_BAD_REQUEST)
        
        # ... (Lógica de credenciais e refresh, idêntica à de cima) ...
        credentials = Credentials(token=token_google.access_token, refresh_token=token_google.refresh_token, token_uri='https://oauth2.googleapis.com/token', client_id=settings.GOOGLE_CLIENT_ID, client_secret=settings.GOOGLE_CLIENT_SECRET)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_google.access_token = credentials.token
            token_google.save()

        evento_data = request.data
        evento_atualizado = {
            'summary': evento_data.get('title'),
            'description': evento_data.get('description'),
            'start': {'dateTime': evento_data.get('start'), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': evento_data.get('end'), 'timeZone': 'America/Sao_Paulo'},
        }

        if evento_data.get('location'):
            evento_atualizado['location'] = evento_data.get('location')

        try:
            service = build('calendar', 'v3', credentials=credentials)
            service.events().patch(calendarId='primary', eventId=eventId, body=evento_atualizado).execute()
            return Response({'status': 'success', 'detail': 'Evento atualizado com sucesso!'})
        except HttpError as error:
            return Response({'detail': f'Falha ao atualizar evento: {error}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request, eventId, *args, **kwargs):
        user = request.user
        try:
            token_google = GoogleApiToken.objects.get(usuario=user)
        except GoogleApiToken.DoesNotExist:
            return Response({'detail': 'Autorização do Google não encontrada.'}, status=status.HTTP_400_BAD_REQUEST)

        # ... (Lógica de credenciais e refresh, idêntica à de cima) ...
        credentials = Credentials(token=token_google.access_token, refresh_token=token_google.refresh_token, token_uri='https://oauth2.googleapis.com/token', client_id=settings.GOOGLE_CLIENT_ID, client_secret=settings.GOOGLE_CLIENT_SECRET)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_google.access_token = credentials.token
            token_google.save()

        try:
            service = build('calendar', 'v3', credentials=credentials)
            service.events().delete(calendarId='primary', eventId=eventId).execute()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except HttpError as error:
            return Response({'detail': f'Falha ao excluir evento: {error}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SharedGoogleAgendaView(APIView):
    """
    Retorna os eventos do Google Agenda associado a uma Conta específica,
    apenas para usuários autorizados.
    """
    permission_classes = [permissions.IsAuthenticated, CanViewSharedAgenda]

    def get(self, request, conta_id, *args, **kwargs):
        try:
            # 1. Encontra a conta e o ID do calendário dela
            conta = Conta.objects.get(pk=conta_id)
            calendar_id = conta.google_calendar_id
            
            if not calendar_id:
                return Response({'detail': 'Esta conta não possui uma agenda do Google configurada.'}, status=status.HTTP_404_NOT_FOUND)

            # 2. Usa o token de um usuário administrador para acessar a agenda
            admin_user = User.objects.filter(is_superuser=True).order_by('id').first()
            if not admin_user:
                 return Response({'detail': 'Nenhum superusuário encontrado para autenticação com o Google.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            token_google = GoogleApiToken.objects.get(usuario=admin_user)
            
            # 3. Prepara as credenciais (CÓDIGO CORRIGIDO)
            credentials = Credentials(
                token=token_google.access_token,
                refresh_token=token_google.refresh_token,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=settings.GOOGLE_CLIENT_ID,
                client_secret=settings.GOOGLE_CLIENT_SECRET
            )

            if credentials.expired and credentials.refresh_token:
                credentials.refresh(GoogleAuthRequest()) # Isso agora funciona por causa do import
                token_google.access_token = credentials.token
                token_google.save()

            # 4. Busca os eventos no Google Calendar
            service = build('calendar', 'v3', credentials=credentials)
            
            now = timezone.now()
            # CORREÇÃO NO FORMATO DE DATA: timezone.now() já inclui fuso horário,
            # então .isoformat() já é o formato correto.
            time_min = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
            time_max = (now + timedelta(days=90)).isoformat()

            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            
            # Formata os eventos para o FullCalendar
            eventos_formatados = []
            for event in events:
                summary = event.get('summary', '')
                
                if summary.strip().startswith('Particular'):
                    continue

                start = event['start'].get('dateTime', event['start'].get('date'))
                end = event['end'].get('dateTime', event['end'].get('date'))
                eventos_formatados.append({
                    'id': event['id'],
                    'title': event.get('summary', 'Sem Título'),
                    'start': start,
                    'end': end,
                    'allDay': 'date' in event['start'],
                    'description': event.get('description', ''),
                    'location': event.get('location', ''),
                    'htmlLink': event.get('htmlLink', '')
                })

            return Response(eventos_formatados)

        except Conta.DoesNotExist:
            return Response({'detail': 'Conta não encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        except GoogleApiToken.DoesNotExist:
            return Response({'detail': 'Token de serviço do Google para o administrador não configurado.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except HttpError as e:
            return Response({'detail': f'Ocorreu um erro ao comunicar com a API do Google: {e}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({'detail': f'Ocorreu um erro inesperado: {e}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class AgendasCompartilhadasListView(generics.ListAPIView):
    """
    Retorna uma lista de Contas cujas agendas o usuário logado
    tem permissão explícita para visualizar.
    """
    serializer_class = ContaSerializer # Reutilizamos o serializer que já existe
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        
        # Se o usuário não tiver um perfil, não pode ver nenhuma agenda
        if not hasattr(user, 'perfil'):
            return Conta.objects.none()

        # A MÁGICA ACONTECE AQUI:
        # Filtra as contas do perfil do usuário para pegar apenas aquelas
        # onde a permissão explícita 'pode_visualizar_agendas_compartilhadas' é True.
        return user.perfil.contas.filter(
            perfilusuario__pode_visualizar_agendas_compartilhadas=True
        ).distinct()

class EspacoAgendaView(generics.ListAPIView):
    """
    Retorna todas as agendas confirmadas para um espaço específico,
    em um formato compatível com calendários.
    """
    serializer_class = EspacoAgendaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Pega o ID do espaço a partir da URL
        espaco_id = self.kwargs.get('espaco_id')
        
        # Retorna apenas as solicitações que estão com o status 'AGENDADO',
        # que pertencem ao espaço solicitado e que possuem uma data definida.
        return SolicitacaoAgenda.objects.filter(
            espaco__id=espaco_id,
            status='AGENDADO',
            data_agendada__isnull=False,
            data_agendada_fim__isnull=False
        )
    
class MunicipeCheckDuplicatesView(ListAPIView):
    """
    Endpoint para verificar a existência de contatos duplicados antes da criação,
    agora com a regra de negócio correta baseada no contexto da Conta.
    """
    serializer_class = MunicipeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # 1. Pega os dados enviados pelo frontend
        nome = self.request.query_params.get('nome_completo', None)
        cpf = self.request.query_params.get('cpf', None)
        email = self.request.query_params.get('email', None)
        telefone_str = self.request.query_params.get('telefone', None)
        contas_id_contexto_str = self.request.query_params.get('conta_id', None)

        # 2. Constrói a primeira parte da consulta: encontrar por dados pessoais
        query_dados_pessoais = Q()
        if cpf and len(cpf) > 10:
            query_dados_pessoais |= Q(cpf=cpf)
        if nome:
            query_dados_pessoais |= Q(nome_completo__iexact=nome)
        if email:
            query_dados_pessoais |= Q(emails__contains=[{'email': email}])
        if telefone_str and len(telefone_str) > 10:
             query_dados_pessoais |= Q(telefones__contains=[{'numero': telefone_str}])

        # Se nenhum critério de busca foi fornecido, não há duplicatas a verificar
        if not query_dados_pessoais:
            return Municipe.objects.none()
        
        # --- AQUI ESTÁ A LÓGICA CORRETA ---
        # 3. Constrói a segunda parte da consulta: o contexto da(s) conta(s)
        if not contas_id_contexto_str:
            # Se o frontend não enviar um contexto, não podemos verificar duplicatas contextuais.
            return Municipe.objects.none()
        
        try:
            # Converte a string '1,2,3' em uma lista de inteiros [1, 2, 3]
            contas_ids = [int(id_str) for id_str in contas_id_contexto_str.split(',') if id_str.strip()]
            if not contas_ids:
                return Municipe.objects.none()
            
            # 4. A MÁGICA FINAL: Combina as duas consultas com um "E" (AND).
            #    A busca final é: "Encontre um munícipe que corresponda aos dados pessoais
            #    E que esteja vinculado a pelo menos uma das contas do contexto."
            queryset = Municipe.objects.filter(
                query_dados_pessoais & Q(contas__id__in=contas_ids)
            ).distinct()
            
            return queryset
            
        except (ValueError, TypeError):
            # Se os IDs das contas forem inválidos, retorna uma lista vazia
            return Municipe.objects.none()
        # --- FIM DA LÓGICA CORRETA ---

class ReservaEspacoListCreateView(generics.ListCreateAPIView):
    serializer_class = ReservaEspacoSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageReservas]

    def get_queryset(self):
        user = self.request.user
        # Começa com todas as reservas
        queryset = ReservaEspaco.objects.all().order_by('data_inicio')

        # Filtra para mostrar apenas reservas de espaços que o usuário pode ver
        if not user.is_superuser:
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(espaco__contas__in=user.perfil.contas.all()).distinct()
            else:
                queryset = ReservaEspaco.objects.none()

        # Mantém o filtro por ID de espaço que já tínhamos
        espaco_id = self.request.query_params.get('espaco', None)
        if espaco_id:
            queryset = queryset.filter(espaco__id=espaco_id)
            
        return queryset

    def perform_create(self, serializer):
        serializer.save(responsavel=self.request.user)

class ReservaEspacoDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = ReservaEspaco.objects.all()
    serializer_class = ReservaEspacoSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageReservas]

class RemoverLinkGoogleView(APIView):
    """
    Remove o link do Google Agenda de uma solicitação específica.
    """
    permission_classes = [permissions.IsAuthenticated, CanManageAgendas] # Reutilizamos a permissão existente

    def post(self, request, pk, *args, **kwargs):
        try:
            solicitacao = SolicitacaoAgenda.objects.get(pk=pk)
            
            # Limpa o campo do link e salva
            solicitacao.link_google_agenda = None
            solicitacao.save()
            
            # Retorna a solicitação atualizada para o frontend
            serializer = SolicitacaoAgendaSerializer(solicitacao)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except SolicitacaoAgenda.DoesNotExist:
            return Response({'detail': 'Solicitação não encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'detail': f'Ocorreu um erro: {e}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)