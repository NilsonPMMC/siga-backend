import os
import re
import csv
import base64
from urllib.parse import quote
import openpyxl
import calendar
import operator
import traceback
import logging
import uuid
import threading
import unicodedata

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
from django.db.models.functions import Trim, TruncMonth, TruncDay, Coalesce
from django.conf import settings
from django.contrib.sites.models import Site
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db.models import Count, Q, Value, F, CharField, ProtectedError
from django.http import HttpResponse
from django.shortcuts import redirect, get_object_or_404
from django.template.loader import render_to_string
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.db import transaction, models, IntegrityError
from django.db.models.fields.related import ManyToManyField, ForeignKey
from django.apps import apps
from django.core.management import call_command
from itertools import chain

# Imports do Django REST Framework
from rest_framework import generics, permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.pagination import PageNumberPagination

from .pagination import SIGAListPagination
from rest_framework.generics import ListAPIView
from rest_framework.decorators import action

# Imports locais (do seu projeto)
from .utils import enviar_email_com_cid
from .report_calendar_utils import build_meses_do_relatorio
from oficios.models import Oficio

# Configurar logger
logger = logging.getLogger(__name__)
from .models import *
from .utils_dados import is_data_dirty
from .permissions import (CanAccessContacts, CanAccessContactsAvancado, CanManageCategoriasContato,
                          CanAccessObjectByConta, CanViewSharedAgenda, CanAccessEspaco,
                          CanInteractWithAtendimento, CanManageAgendas, CanCreateGoogleEvent, CanManageReservas,
                          CanViewAgendaReports, CanViewAtendimentoReports, CanEditMunicipeDetails, CanManageCheckIn,
                          CanCreateCheckIn, CanManageLembretes, CanViewCrmLogs, is_in_group)
from .serializers import *
from .services import GoogleCalendarCompatibilityService, GoogleCalendarAuthError, GoogleCalendarPermissionError
from .services.log_crm import suppress_crm_logs, registrar_log_mesclagem_municipes
from .services.unificar_municipes import preview_unificacao_municipes


# -----------------------------------------------------------------------------
# Views de Atendimento
# -----------------------------------------------------------------------------

class AtendimentoListCreateView(generics.ListCreateAPIView):
    serializer_class = AtendimentoSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = SIGAListPagination

    def get_queryset(self):
        user = self.request.user
        queryset = Atendimento.objects.select_related(
            'municipe', 'conta', 'responsavel', 'assunto'
        ).prefetch_related('responsaveis_compartilhados')

        # REGRA 1: Superusuário vê tudo.
        if user.is_superuser:
            queryset = queryset.all()
        # REGRA 2: Se for da Recepção, mostra TODOS os atendimentos das contas vinculadas.
        elif is_in_group(user, 'Recepção'):
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
            else:
                return Atendimento.objects.none()
        # REGRA 3: A regra para Membros e Secretárias
        elif hasattr(user, 'perfil'):
            atendimentos_da_conta = queryset.filter(conta__in=user.perfil.contas.all())
            queryset = atendimentos_da_conta.filter(
                Q(responsavel=user) | Q(responsavel__isnull=True) | Q(responsaveis_compartilhados=user)
            ).distinct()
        else:
            # REGRA 4: Se nenhuma das anteriores se aplicar, não mostra nada.
            return Atendimento.objects.none()

        termo_busca = self.request.query_params.get('q', None)
        if termo_busca:
            from .services.busca_textual import filtrar_queryset_atendimento

            queryset = filtrar_queryset_atendimento(queryset, termo_busca)

        # Aplicar filtro de status (se fornecido)
        status = self.request.query_params.get('status', None)
        if status:
            queryset = queryset.filter(status=status)

        # Aplicar filtro de conta (se fornecido)
        conta_id = self.request.query_params.get('conta_id', None)
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)

        assunto_id = self.request.query_params.get('assunto_id', None)
        if assunto_id:
            queryset = queryset.filter(assunto_id=assunto_id)

        assunto_codigo = self.request.query_params.get('assunto_codigo', None)
        if assunto_codigo:
            queryset = queryset.filter(assunto__codigo=assunto_codigo)

        sla_status = self.request.query_params.get('sla_status', None)
        if sla_status:
            from .services.sla_atendimento import filtrar_queryset_por_sla
            queryset = filtrar_queryset_por_sla(queryset, sla_status)

        ordering = self.request.query_params.get('ordering', '-data_criacao')
        campos_ordenacao = {
            'protocolo', '-protocolo',
            'data_criacao', '-data_criacao',
            'status', '-status',
            'titulo', '-titulo',
        }
        if ordering in campos_ordenacao:
            return queryset.order_by(ordering)
        return queryset.order_by('-data_criacao')

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class AtendimentoDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AtendimentoSerializer
    permission_classes = [permissions.IsAuthenticated, CanInteractWithAtendimento]

    def get_queryset(self):
        return Atendimento.objects.select_related(
            'municipe', 'conta', 'responsavel', 'assunto', 'assunto_ia_sugerido'
        ).prefetch_related('responsaveis_compartilhados', 'tramitacoes__usuario', 'anexos')


class SugerirAssuntoAtendimentoView(APIView):
    """Sugere (e opcionalmente aplica) o assunto de um atendimento via LLM."""
    permission_classes = [permissions.IsAuthenticated, CanInteractWithAtendimento]

    def post(self, request, pk):
        atendimento = get_object_or_404(Atendimento, pk=pk)
        self.check_object_permissions(request, atendimento)
        aplicar = (request.query_params.get('aplicar') or '').lower() in ('1', 'true', 'sim')
        from .services.assunto_ia import sugerir_assunto_atendimento

        resultado = sugerir_assunto_atendimento(atendimento, aplicar=aplicar)
        if not resultado.get('ok'):
            return Response(resultado, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        atendimento.refresh_from_db()
        data = AtendimentoSerializer(atendimento, context={'request': request}).data
        data['sugestao_ia'] = resultado
        return Response(data)


class SugerirAssuntoPreviewView(APIView):
    """Sugestão de assunto a partir de título/descrição (antes de salvar o atendimento)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        titulo = (request.data.get('titulo') or '').strip()
        descricao = (request.data.get('descricao') or '').strip()
        if not titulo and not descricao:
            return Response(
                {'detail': 'Informe título ou descrição para a sugestão.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        origem = (request.data.get('origem') or 'PRESENCIAL').strip()
        municipe_id = request.data.get('municipe')
        conta_id = request.data.get('conta')
        try:
            municipe_id = int(municipe_id) if municipe_id is not None else None
        except (TypeError, ValueError):
            municipe_id = None
        try:
            conta_id = int(conta_id) if conta_id is not None else None
        except (TypeError, ValueError):
            conta_id = None

        from .services.assunto_ia import sugerir_assunto_preview

        resultado = sugerir_assunto_preview(
            titulo=titulo,
            descricao=descricao,
            origem=origem,
            municipe_id=municipe_id,
            conta_id=conta_id,
        )
        if not resultado.get('ok'):
            return Response(resultado, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(resultado)


class RecarregarResumoAtendimentoView(APIView):
    """Gera/recarrega o resumo IA e vetor do atendimento via Ollama."""
    permission_classes = [permissions.IsAuthenticated, CanInteractWithAtendimento]

    def post(self, request, pk):
        atendimento = get_object_or_404(Atendimento, pk=pk)
        with transaction.atomic():
            from .services.ia_intelligence import gerar_resumo_atendimento, atualizar_vetor_atendimento
            resumo = gerar_resumo_atendimento(atendimento)
            if resumo is None:
                atendimento.auditoria_ia_status = 'ERRO'
                atendimento.save(update_fields=['auditoria_ia_status'])
                return Response({'detail': 'Erro ao gerar resumo. Verifique se o Ollama está rodando.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            atendimento.resumo_ia_local = resumo
            atendimento.save(update_fields=['resumo_ia_local'])
            atualizar_vetor_atendimento(atendimento)
            atendimento.auditoria_ia_status = 'PROCESSADO'
            atendimento.save(update_fields=['auditoria_ia_status'])
        serializer = AtendimentoSerializer(atendimento)
        return Response(serializer.data)


class BuscaSemanticaAtendimentosView(APIView):
    """Busca semântica de atendimentos por conta (IA) - usa buscar_atendimentos_semantico_otimizado."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        q = (request.query_params.get('q') or '').strip()
        conta_id_param = request.query_params.get('conta_id')

        if not q:
            return Response({'detail': 'Parâmetro q é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        conta_id = None

        if conta_id_param:
            try:
                conta_id = int(conta_id_param)
            except (TypeError, ValueError):
                return Response({'detail': 'conta_id inválido.'}, status=status.HTTP_400_BAD_REQUEST)
            if not user.is_superuser and hasattr(user, 'perfil'):
                contas_ids = list(user.perfil.contas.values_list('id', flat=True))
                if conta_id not in contas_ids:
                    return Response({'detail': 'Sem permissão para esta conta.'}, status=status.HTTP_403_FORBIDDEN)
        else:
            # Superuser pode buscar sem conta_id (todos os atendimentos)
            if not user.is_superuser:
                if hasattr(user, 'perfil'):
                    primeira_conta = user.perfil.contas.first()
                    if primeira_conta:
                        conta_id = primeira_conta.id
                if conta_id is None:
                    return Response(
                        {'detail': 'conta_id é obrigatório ou configure seu perfil com ao menos uma conta.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        from .services.ia_intelligence import buscar_atendimentos_semantico_otimizado
        resultados_raw = buscar_atendimentos_semantico_otimizado(q, conta_id=conta_id, top_k=10)

        # Serializa cada atendimento com AtendimentoSerializer e adiciona score_match
        payload = []
        for item in resultados_raw:
            data = AtendimentoSerializer(item['atendimento']).data
            data['score_match'] = item['score_percentual']
            data['snippet'] = item.get('snippet', '')
            payload.append(data)

        return Response(payload)


class BuscaIACrmView(APIView):
    """Busca semântica de munícipes (CRM) - retorna Nome, Cargo, Telefone, Bairro e score."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        q = (request.query_params.get('q') or '').strip()
        try:
            limite = min(int(request.query_params.get('limite', 20) or 20), 50)
        except (TypeError, ValueError):
            limite = 20

        if not q:
            return Response({'detail': 'Parâmetro q é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from .services.ia_intelligence import buscar_municipes_semantico
            resultados_raw = buscar_municipes_semantico(q, limite=limite)
        except Exception as e:
            logger.exception("Busca IA CRM falhou: %s", e)
            return Response(
                {'detail': 'Erro ao gerar embedding da query. Verifique se o Ollama está rodando.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload = []
        for item in resultados_raw:
            m = item['municipe']
            cargo = _formatar_cargo_orgao_municipe(m)
            telefone = ''
            if m.telefones and isinstance(m.telefones, list):
                prim = next((t for t in m.telefones if isinstance(t, dict) and t.get('numero')), None)
                if prim:
                    telefone = str(prim.get('numero', ''))
            bairro = ''
            if m.endereco and isinstance(m.endereco, dict):
                bairro = (m.endereco.get('bairro') or m.endereco.get('bairro_nome') or '').strip()

            payload.append({
                'id': m.id,
                'nome': m.nome_completo or '',
                'cargo': cargo,
                'telefone': telefone,
                'bairro': bairro,
                'score_match': item['score_percentual'],
            })

        return Response(payload)


class RegistroVisitaListCreateView(generics.ListCreateAPIView):
    serializer_class = RegistroVisitaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), CanCreateCheckIn()]
        return [permissions.IsAuthenticated(), CanManageCheckIn()]

    def get_queryset(self):
        user = self.request.user
        queryset = RegistroVisita.objects.select_related(
            'municipe', 'conta_destino', 'usuario_destino', 'registrado_por'
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

    @transaction.atomic
    def perform_create(self, serializer):
        from .services.visita_atendimento import registrar_visita_como_atendimento

        data = serializer.validated_data
        atendimento, registro = registrar_visita_como_atendimento(
            municipe=data['municipe'],
            conta_destino=data['conta_destino'],
            usuario_destino=data.get('usuario_destino'),
            observacao=data.get('observacao'),
            registrado_por=self.request.user,
            manter_registro_legado=True,
        )
        if registro is None:
            registro = serializer.save(registrado_por=self.request.user)

        nome_municipe = atendimento.municipe.nome_completo
        mensagem = f"O Munícipe {nome_municipe} acabou de chegar para uma visita/reunião."
        link = f"/atendimentos/{atendimento.id}"
        usuarios_notificar = []
        if atendimento.responsavel_id:
            usuarios_notificar = [atendimento.responsavel]
        else:
            usuarios_notificar = list(
                User.objects.filter(perfil__contas=atendimento.conta).distinct()
            )
        for usuario in usuarios_notificar:
            Notificacao.objects.create(usuario=usuario, mensagem=mensagem, link=link)

        self._registro_criado = registro
        self._atendimento_criado = atendimento

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        registro = getattr(self, '_registro_criado', None)
        output = RegistroVisitaSerializer(registro, context={'request': request})
        headers = self.get_success_headers(output.data)
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)

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
        queryset = SolicitacaoAgenda.objects.select_related('solicitante').prefetch_related('solicitante__perfis')

        # Seus filtros de busca por data, conta e status continuam perfeitos
        data_inicio = self.request.query_params.get('data_inicio', None)
        data_fim = self.request.query_params.get('data_fim', None)
        conta_id = self.request.query_params.get('conta_id', None)
        status_list = self.request.query_params.getlist('status', None)

        if data_inicio and data_fim:
            queryset = queryset.filter(data_criacao__date__range=[data_inicio, data_fim])
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)
        if status_list:
            queryset = queryset.filter(status__in=status_list)

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
    serializer_class = UserSerializer

    def get_queryset(self):
        queryset = User.objects.filter(is_active=True).order_by('username')
        conta_id = self.request.query_params.get('conta_id', None)
        if conta_id:
            try:
                conta_id = int(conta_id)
            except (TypeError, ValueError):
                return queryset
            user = self.request.user
            if user.is_superuser:
                queryset = queryset.filter(perfil__contas__id=conta_id).distinct()
            elif hasattr(user, 'perfil') and user.perfil.contas.filter(id=conta_id).exists():
                # Usuário tem acesso à conta: pode listar membros da mesma
                queryset = queryset.filter(perfil__contas__id=conta_id).distinct()
        return queryset

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
    serializer_class = ContaSerializer

    def get_queryset(self):
        # 1. Base QuerySet
        queryset = Conta.objects.all().order_by('nome')

        # 2. Filtro Manual para Escala
        participa_escala = self.request.query_params.get('participa_escala')
        if participa_escala is not None:
            # Converte string 'true'/'false' para booleano
            is_active = participa_escala.lower() == 'true'
            queryset = queryset.filter(participa_escala=is_active)

        return queryset


class CategoriaAtendimentoListView(generics.ListAPIView):
    """Deprecated (Fase 8): use GET /api/assuntos-atendimento/."""
    permission_classes = [permissions.IsAuthenticated]
    queryset = CategoriaAtendimento.objects.none()
    serializer_class = CategoriaAtendimentoSerializer

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response['Deprecation'] = 'true'
        response['Link'] = '</api/assuntos-atendimento/>; rel="successor-version"'
        return response


class AssuntoAtendimentoListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    queryset = AssuntoAtendimento.objects.filter(ativo=True)
    serializer_class = AssuntoAtendimentoSerializer


class CategoriaContatoListView(generics.ListAPIView):
    serializer_class = CategoriaContatoSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from .services.escopo_operador_crm import categorias_escopo_usuario

        qs = CategoriaContato.objects.filter(ativa=True)
        escopo = categorias_escopo_usuario(self.request.user)
        if escopo is not None:
            return qs.filter(id__in=escopo)
        return qs

class CategoriaContatoViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gerenciar (CRUD) as Categorias de Contato.
    Operador CRM: somente leitura das categorias permitidas no perfil.
    """
    queryset = CategoriaContato.objects.all().order_by('nome')
    serializer_class = CategoriaContatoSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.request.method not in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated(), CanManageCategoriasContato()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        from .services.escopo_operador_crm import categorias_escopo_usuario

        qs = super().get_queryset()
        escopo = categorias_escopo_usuario(self.request.user)
        if escopo is not None:
            return qs.filter(id__in=escopo, ativa=True)
        return qs

    def _categorias_resumo_queryset(self, request):
        categoria_ids = request.query_params.getlist("categoria_id")
        categorias_qs = CategoriaContato.objects.all().order_by("nome")
        if categoria_ids:
            categorias_qs = categorias_qs.filter(id__in=categoria_ids)

        # Alinhado com a visão de categorias da operação: considera perfis ativos,
        # independentemente do status ativo/inativo do munícipe.
        perfis_qs = PerfilMunicipe.objects.filter(ativo=True)
        if not request.user.is_superuser:
            if hasattr(request.user, "perfil"):
                perfis_qs = perfis_qs.filter(conta__in=request.user.perfil.contas.all())
            else:
                perfis_qs = PerfilMunicipe.objects.none()
        if categoria_ids:
            perfis_qs = perfis_qs.filter(categoria_id__in=categoria_ids)

        totals = dict(
            perfis_qs.values("categoria_id")
            .annotate(total=Count("municipe_id", distinct=True))
            .values_list("categoria_id", "total")
        )

        rows = []
        for categoria in categorias_qs:
            rows.append(
                {
                    "categoria_id": categoria.id,
                    "categoria": categoria.nome,
                    "contatos_total": int(totals.get(categoria.id, 0)),
                }
            )
        return rows

    @action(detail=False, methods=["get"], url_path="relatorio-csv")
    def relatorio_csv(self, request):
        rows = self._categorias_resumo_queryset(request)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response.write("\ufeff")
        response["Content-Disposition"] = 'attachment; filename="relatorio_categorias_contatos.csv"'
        writer = csv.writer(response, delimiter=";")
        writer.writerow(["categoria", "contatos (total)"])
        for row in rows:
            writer.writerow([row["categoria"], row["contatos_total"]])
        return response

    @action(detail=False, methods=["get"], url_path="relatorio-pdf")
    def relatorio_pdf(self, request):
        rows = self._categorias_resumo_queryset(request)
        conta_contexto = None
        if request.user.is_superuser:
            conta_contexto = Conta.objects.filter(ativo=True).order_by("id").first()
        elif hasattr(request.user, "perfil"):
            conta_contexto = request.user.perfil.contas.order_by("id").first()

        brasao_url = ""
        if conta_contexto and getattr(conta_contexto, "brasao_instituicao", None):
            try:
                if conta_contexto.brasao_instituicao and os.path.exists(conta_contexto.brasao_instituicao.path):
                    brasao_url = f"file://{os.path.abspath(conta_contexto.brasao_instituicao.path)}"
                else:
                    brasao_url = request.build_absolute_uri(conta_contexto.brasao_instituicao.url)
            except Exception:
                brasao_url = ""

        logo_siga_url = ""
        candidatos_logo = [
            os.path.join(str(settings.STATIC_ROOT), "images", "logo-siga-gab.png"),
            os.path.join(str(settings.BASE_DIR), "staticfiles", "images", "logo-siga-gab.png"),
            os.path.join(str(settings.BASE_DIR), "atendimentos", "static", "images", "logo-siga-gab.png"),
        ]
        for caminho in candidatos_logo:
            if caminho and os.path.exists(caminho):
                logo_siga_url = f"file://{os.path.abspath(caminho)}"
                break
        if not logo_siga_url:
            # fallback robusto para não quebrar o layout se o arquivo de logo não existir no servidor
            svg = (
                "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='24'>"
                "<rect width='100' height='24' rx='4' fill='#1d4ed8'/>"
                "<text x='50' y='16' text-anchor='middle' font-size='12' fill='white' "
                "font-family='Arial, sans-serif'>SIGA</text></svg>"
            )
            logo_siga_url = f"data:image/svg+xml;utf8,{quote(svg)}"

        context = {
            "linhas": rows,
            "total_contatos": sum(r["contatos_total"] for r in rows),
            "gerado_em": timezone.localtime(),
            "usuario_emissao": request.user.get_full_name() or request.user.username,
            "brasao_url": brasao_url,
            "logo_siga_url": logo_siga_url,
        }
        html = render_to_string("relatorios/categorias_contatos_report.html", context)
        pdf = HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="relatorio_categorias_contatos.pdf"'
        return response


class LogDeAtividadeViewSet(viewsets.ReadOnlyModelViewSet):
    """Consulta de logs de auditoria do CRM (Épico 11)."""
    serializer_class = LogDeAtividadeSerializer
    permission_classes = [permissions.IsAuthenticated, CanViewCrmLogs]
    pagination_class = SIGAListPagination

    def get_queryset(self):
        qs = LogDeAtividade.objects.select_related(
            'usuario', 'conta', 'content_type'
        ).order_by('-timestamp')

        acao = self.request.query_params.get('acao')
        if acao:
            qs = qs.filter(acao=acao)

        entidade = self.request.query_params.get('entidade')
        if entidade:
            qs = qs.filter(content_type__model=entidade.lower())

        conta_id = self.request.query_params.get('conta_id')
        if conta_id:
            qs = qs.filter(conta_id=conta_id)

        de = self.request.query_params.get('de')
        if de:
            qs = qs.filter(timestamp__date__gte=de)

        ate = self.request.query_params.get('ate')
        if ate:
            qs = qs.filter(timestamp__date__lte=ate)

        user = self.request.user
        if not user.is_superuser and hasattr(user, 'perfil'):
            contas = user.perfil.contas.all()
            qs = qs.filter(Q(conta__in=contas) | Q(conta__isnull=True))

        return qs

# -----------------------------------------------------------------------------
# Views de Munícipe
# -----------------------------------------------------------------------------

def _categoria_ids_from_request(request):
    from .services.perfil_municipe import parse_categoria_ids_from_request
    return parse_categoria_ids_from_request(request)


class MunicipeListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]
    serializer_class = MunicipeSerializer
    pagination_class = SIGAListPagination

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('tem_grupo_duplicado') == 'true':
            return None
        return super().paginate_queryset(queryset)

    def get_queryset(self):
        user = self.request.user
        termo_busca = self.request.query_params.get('q', None) or self.request.query_params.get('q_sem_acento', None)
        letra_inicial = self.request.query_params.get('letra', None)
        grupo_id = self.request.query_params.get('grupo', None)
        tem_grupo_duplicado = self.request.query_params.get('tem_grupo_duplicado', None)
        categoria_ids = _categoria_ids_from_request(self.request)

        filtro_aplicado = bool(termo_busca or letra_inicial or categoria_ids)

        base_queryset = Municipe.objects.prefetch_related('contas', 'perfis', 'perfis__categoria')

        if grupo_id:
            base_queryset = base_queryset.filter(grupo_duplicado=grupo_id)
        elif tem_grupo_duplicado == 'true':
            base_queryset = base_queryset.exclude(grupo_duplicado__isnull=True)

        if user.is_superuser:
            pass
        elif hasattr(user, 'perfil'):
            from .services.escopo_operador_crm import aplicar_escopo_municipes_queryset

            base_queryset = aplicar_escopo_municipes_queryset(
                base_queryset, user, categoria_ids_opcional=categoria_ids or None
            )
        else:
            return Municipe.objects.none()

        if categoria_ids and user.is_superuser:
            from .services.perfil_municipe import filtrar_municipes_por_categoria_perfis

            base_queryset = filtrar_municipes_por_categoria_perfis(
                base_queryset, categoria_ids, None
            )
        elif categoria_ids and not hasattr(user, 'perfil'):
            return Municipe.objects.none()

        if termo_busca:
            from .services.busca_textual import filtrar_queryset_municipe

            if tem_grupo_duplicado == 'true':
                matches = filtrar_queryset_municipe(base_queryset, termo_busca)
                grupo_ids = (
                    matches.exclude(grupo_duplicado__isnull=True)
                    .values_list('grupo_duplicado', flat=True)
                    .distinct()
                )
                base_queryset = (
                    base_queryset.filter(grupo_duplicado__in=grupo_ids)
                    if grupo_ids
                    else base_queryset.none()
                )
            else:
                base_queryset = filtrar_queryset_municipe(base_queryset, termo_busca).distinct()
        
        if letra_inicial:
            base_queryset = base_queryset.filter(nome_completo__istartswith=letra_inicial)

        ordenar_por = self.request.query_params.get('ordenar_por', 'nome')
        if grupo_id or tem_grupo_duplicado == 'true':
            return base_queryset.order_by('grupo_duplicado', 'nome_completo')
        if ordenar_por == 'orgao':
            return base_queryset.order_by('orgao', 'nome_completo')
        if filtro_aplicado:
            return base_queryset.order_by('nome_completo')
        return base_queryset.order_by('-data_cadastro')

    # --- O AJUSTE ESTÁ AQUI EMBAIXO ---
    def create(self, request, *args, **kwargs):
        """
        Sobrescreve create para adicionar aviso de qualidade de dados no response.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        
        # Verificar qualidade e adicionar aviso ao response se necessário
        municipe = serializer.instance
        aviso_qualidade = self._verificar_qualidade_dados(municipe)
        
        response_data = serializer.data
        if aviso_qualidade:
            response_data['aviso_qualidade'] = aviso_qualidade
        
        return Response(response_data, status=status.HTTP_201_CREATED, headers=headers)
    
    def perform_create(self, serializer):
        """
        Intercepta a criação para garantir que o munícipe seja vinculado
        às contas do usuário logado (se ele não for superuser).
        """
        # 1. Salva o registro inicial (sem M2M ainda)
        municipe = serializer.save()
        user = self.request.user

        # 2. Regra de Vínculo Automático
        # Se não for Admin, força o vínculo com as contas do perfil do usuário.
        if not user.is_superuser:
            if hasattr(user, 'perfil') and user.perfil.contas.exists():
                contas_do_usuario = user.perfil.contas.all()
                # .set() funciona para campos ManyToMany
                municipe.contas.set(contas_do_usuario)
    
    def _verificar_qualidade_dados(self, municipe):
        """
        Verifica qualidade dos dados do munícipe e dispara análise assíncrona se necessário.
        Retorna aviso para incluir no response (não bloqueia salvamento).
        """
        try:
            # Extrair telefone principal
            telefone_principal = ''
            if municipe.telefones and isinstance(municipe.telefones, list) and len(municipe.telefones) > 0:
                telefone_principal = municipe.telefones[0].get('numero', '')
            
            # Verificar se telefone é genérico ou já existe em outros registros
            telefone_genérico = False
            telefone_duplicado = False
            problemas = []
            
            if telefone_principal:
                # Detecção rápida de padrão lixo
                import re
                numeros = re.sub(r'\D', '', telefone_principal)
                if len(numeros) >= 8:
                    # Padrão genérico (99) 99999-9999
                    if re.match(r'^9{2}9{5}9{4}$', numeros) or re.match(r'^9{10,11}$', numeros):
                        telefone_genérico = True
                        problemas.append('Telefone genérico detectado')
                    # Sequências repetitivas
                    elif len(set(numeros)) == 1:
                        telefone_genérico = True
                        problemas.append('Telefone com sequência repetitiva')
                    # Sequências óbvias
                    elif re.match(r'^12345678', numeros) or re.match(r'^123456789', numeros):
                        telefone_genérico = True
                        problemas.append('Telefone com sequência óbvia')
                
                # Verificar se telefone já existe em outros 5 registros
                outros_com_mesmo_telefone = Municipe.objects.exclude(id=municipe.id).filter(
                    telefones__contains=[{'numero': telefone_principal}]
                )[:5]
                if outros_com_mesmo_telefone.exists():
                    telefone_duplicado = True
                    problemas.append(f'Telefone já existe em {outros_com_mesmo_telefone.count()} outro(s) registro(s)')
            
            if not municipe.cpf or not municipe.cpf.strip():
                problemas.append('CPF não informado')
            
            # Se detectou problemas, retornar aviso
            if telefone_genérico or telefone_duplicado or not municipe.cpf:
                return {
                    'tipo': 'baixa_qualidade',
                    'mensagem': 'Este registro apresenta indicadores de baixa qualidade de dados.',
                    'problemas': problemas,
                    'acao_recomendada': 'Verifique os dados informados e complete as informações faltantes quando possível.'
                }
            
            return None
                    
        except Exception as e:
            logger.warning(f"Erro ao verificar qualidade de dados do munícipe {municipe.id}: {e}")
            return None

class MunicipeDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated, CanEditMunicipeDetails]
    serializer_class = MunicipeSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Municipe.objects.prefetch_related('contas', 'perfis', 'perfis__categoria')
        from .services.escopo_operador_crm import aplicar_escopo_municipes_queryset

        return aplicar_escopo_municipes_queryset(qs, user)


class MunicipeDetailDataView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]
    serializer_class = MunicipeDetailSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Municipe.objects.prefetch_related(
            'contas', 'perfis', 'perfis__categoria',
            'visitas', 'visitas__conta_destino', 'visitas__usuario_destino',
            'agendas_participantes', 'agendas_participantes__compromisso', 'agendas_participantes__compromisso__conta'
        )
        from .services.escopo_operador_crm import aplicar_escopo_municipes_queryset

        return aplicar_escopo_municipes_queryset(qs, user)


class MunicipeLookupView(generics.ListAPIView):
    serializer_class = MunicipeLookupSerializer
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]

    def get_queryset(self):
        user = self.request.user
        queryset = Municipe.objects.all()

        from .services.escopo_operador_crm import aplicar_escopo_municipes_queryset

        queryset = aplicar_escopo_municipes_queryset(queryset, user)
        queryset = queryset.prefetch_related('contas', 'perfis', 'perfis__categoria')

        # --- 2. FILTRO PARA EXCLUIR IDS (útil para unificação de municipes) ---
        exclude_id = self.request.query_params.get('exclude_id', None)
        if exclude_id:
            try:
                exclude_id_int = int(exclude_id)
                queryset = queryset.exclude(id=exclude_id_int)
            except (ValueError, TypeError):
                pass  # Se não for um ID válido, ignora o filtro
        
        # --- 3. FILTRO POR NOME DA CATEGORIA ---
        categorias_str = self.request.query_params.get('categorias_nome', None)
        if categorias_str:
            nomes = [n.strip() for n in categorias_str.split(',')]
            queryset = queryset.filter(perfis__categoria__nome__in=nomes).distinct()

        # --- 3. BUSCA TEXTUAL ---
        termo_busca = self.request.query_params.get('q', None)

        if not termo_busca:
            return queryset.order_by('-data_cadastro')[:20]

        termo_busca = termo_busca.strip()
        # ID interno só para termos curtos (evita confundir CPF 11 dígitos com pk)
        if termo_busca.isdigit() and len(termo_busca) <= 7:
            por_id = queryset.filter(id=int(termo_busca))
            if por_id.exists():
                return por_id

        from .services.busca_textual import filtrar_queryset_municipe

        resultados = filtrar_queryset_municipe(queryset, termo_busca).distinct()
        return resultados.order_by('nome_completo')[:100]
    
class VerificarDependenciasMunicipeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        municipe = get_object_or_404(Municipe, pk=pk)
        vinculos = []
        total_vinculos = 0

        # Varre os relacionamentos para ver o que tem ligado a este munícipe
        # (Lógica similar ao Unificar, mas apenas contagem)
        for relation in Municipe._meta.get_fields():
            if relation.is_relation and relation.auto_created:
                try:
                    accessor_name = relation.get_accessor_name()
                    # Pega o manager reverso (ex: municipe.atendimentos)
                    manager = getattr(municipe, accessor_name)
                    
                    # Conta quantos registros tem
                    count = manager.count()
                    
                    if count > 0:
                        # Formata nome amigável (ex: "atendimentos" -> "Atendimentos")
                        nome_model = relation.related_model._meta.verbose_name_plural.title()
                        vinculos.append(f"{count} {nome_model}")
                        total_vinculos += count
                except Exception:
                    continue

        return Response({
            "tem_vinculos": total_vinculos > 0,
            "total": total_vinculos,
            "detalhes": vinculos
        })
    
class MesclarDuplicatasView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanAccessContactsAvancado]

    def post(self, request, *args, **kwargs):
        id_principal = request.data.get('id_principal')
        id_duplicado = request.data.get('id_duplicado')

        if not id_principal or not id_duplicado:
            return Response({'error': 'Os IDs do registro principal e do duplicado são obrigatórios.'}, status=status.HTTP_400_BAD_REQUEST)

        if id_principal == id_duplicado:
            return Response({'error': 'O ID principal e o duplicado não podem ser iguais.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            municipe_principal = Municipe.objects.get(pk=id_principal)
            municipe_duplicado = Municipe.objects.get(pk=id_duplicado)
        except Municipe.DoesNotExist:
            return Response({'error': 'Um ou ambos os IDs de Munícipe não foram encontrados.'}, status=status.HTTP_404_NOT_FOUND)

        duplicado_id = municipe_duplicado.id
        duplicado_nome = municipe_duplicado.nome_completo

        try:
            with suppress_crm_logs():
                with transaction.atomic():
                    # Contagens para feedback (antes de transferir)
                    n_atendimentos = municipe_duplicado.atendimentos.count()
                    n_visitas = municipe_duplicado.visitas.count()
                    n_solicitacoes_agenda = municipe_duplicado.solicitacoes_agenda.count()
                    n_perfis = municipe_duplicado.perfis.count()
                    n_reservas = getattr(municipe_duplicado, 'reservas_solicitadas', None)
                    n_reservas = n_reservas.count() if n_reservas is not None else 0

                    # --- 1. DADOS COMPLEMENTARES: foto, observações, data_cadastro ---
                    if not municipe_principal.foto and municipe_duplicado.foto:
                        municipe_principal.foto = municipe_duplicado.foto
                    if municipe_duplicado.observacoes and (municipe_duplicado.observacoes or '').strip():
                        obs_dup = (municipe_duplicado.observacoes or '').strip()
                        if not (municipe_principal.observacoes or '').strip():
                            municipe_principal.observacoes = obs_dup
                        else:
                            municipe_principal.observacoes = (municipe_principal.observacoes or '').strip() + '\n\n[Unificado]\n' + obs_dup
                    # Manter a data de cadastro mais antiga
                    if municipe_duplicado.data_cadastro and municipe_principal.data_cadastro:
                        if municipe_duplicado.data_cadastro < municipe_principal.data_cadastro:
                            municipe_principal.data_cadastro = municipe_duplicado.data_cadastro
                    elif municipe_duplicado.data_cadastro and not municipe_principal.data_cadastro:
                        municipe_principal.data_cadastro = municipe_duplicado.data_cadastro

                    # --- 2. PERFIS (CARGO/ÓRGÃO): herdar todos do duplicado vinculando ao principal ---
                    PerfilMunicipe.objects.filter(municipe=municipe_duplicado).update(municipe=municipe_principal)

                    # --- 3. M2M no próprio Municipe (ex: contas) ---
                    for field in Municipe._meta.many_to_many:
                        manager_duplicado = getattr(municipe_duplicado, field.name)
                        manager_principal = getattr(municipe_principal, field.name)
                        related_objs = manager_duplicado.all()
                        if related_objs.exists():
                            manager_principal.add(*related_objs)

                    # --- 4. Todas as relações reversas (ForeignKey/OneToOne) que apontam para Municipe ---
                    all_related_objects = [
                        f for f in Municipe._meta.get_fields(include_hidden=True)
                        if (f.one_to_many or f.one_to_one or f.many_to_many) and f.auto_created and not f.concrete
                    ]

                    for rel in all_related_objects:
                        if rel.many_to_many and rel.field.model == Municipe:
                            continue
                        try:
                            accessor_name = rel.get_accessor_name()
                            if not hasattr(municipe_duplicado, accessor_name):
                                continue
                        except AttributeError:
                            continue

                        if rel.one_to_many or rel.one_to_one:
                            related_queryset = getattr(municipe_duplicado, accessor_name).all()
                            unique_constraints = getattr(rel.related_model._meta, 'unique_together', [])
                            constraint_fields_to_check = []
                            for constraint in unique_constraints:
                                if rel.field.name in constraint:
                                    constraint_fields_to_check = [f for f in constraint if f != rel.field.name]
                                    break
                            if constraint_fields_to_check:
                                for obj_duplicado in related_queryset:
                                    lookup_filter = {rel.field.name: municipe_principal}
                                    for field_name in constraint_fields_to_check:
                                        lookup_filter[field_name] = getattr(obj_duplicado, field_name)
                                    if rel.related_model.objects.filter(**lookup_filter).exists():
                                        obj_duplicado.delete()
                                    else:
                                        setattr(obj_duplicado, rel.field.name, municipe_principal)
                                        obj_duplicado.save()
                            else:
                                related_queryset.update(**{rel.field.name: municipe_principal})

                        elif rel.many_to_many:
                            related_queryset = getattr(municipe_duplicado, accessor_name).all()
                            for related_obj in related_queryset:
                                m2m_field_on_related = getattr(related_obj, rel.field.name)
                                m2m_field_on_related.add(municipe_principal)
                                m2m_field_on_related.remove(municipe_duplicado)

                    # --- 5. Consolida emails e telefones ---
                    if municipe_principal.emails is None:
                        municipe_principal.emails = []
                    if municipe_principal.telefones is None:
                        municipe_principal.telefones = []

                    emails_principais = {e['email'].lower() for e in municipe_principal.emails if isinstance(e, dict) and e.get('email')}
                    for email_info in (municipe_duplicado.emails or []):
                        if isinstance(email_info, dict) and email_info.get('email') and email_info['email'].lower() not in emails_principais:
                            municipe_principal.emails.append(email_info)

                    telefones_principais = {t['numero'] for t in municipe_principal.telefones if isinstance(t, dict) and t.get('numero')}
                    for tel_info in (municipe_duplicado.telefones or []):
                        if isinstance(tel_info, dict) and tel_info.get('numero') and tel_info['numero'] not in telefones_principais:
                            municipe_principal.telefones.append(tel_info)

                    update_fields = ['foto', 'observacoes', 'data_cadastro', 'emails', 'telefones']
                    municipe_principal.save(update_fields=update_fields)

                    # --- 6. Remove o registro duplicado ---
                    municipe_duplicado.delete()

            registrar_log_mesclagem_municipes(
                request,
                municipe_principal,
                duplicado_id,
                duplicado_nome,
                transferidos={
                    'atendimentos': n_atendimentos,
                    'visitas': n_visitas,
                    'solicitacoes_agenda': n_solicitacoes_agenda,
                    'perfis': n_perfis,
                    'reservas': n_reservas,
                },
            )

            # Resposta com contagem para o toast
            partes = []
            if n_atendimentos:
                partes.append(f'{n_atendimentos} atendimento(s)')
            if n_visitas:
                partes.append(f'{n_visitas} visita(s)')
            if n_solicitacoes_agenda:
                partes.append(f'{n_solicitacoes_agenda} solicitação(ões) de agenda')
            if n_perfis:
                partes.append(f'{n_perfis} cargo(s)/perfil(is)')
            if n_reservas:
                partes.append(f'{n_reservas} reserva(s)')
            msg = ' e '.join(partes) + ' transferidos.' if partes else 'Registros mesclados com sucesso!'

            return Response({
                'status': 'Registros mesclados com sucesso!',
                'transferidos': {
                    'atendimentos': n_atendimentos,
                    'visitas': n_visitas,
                    'solicitacoes_agenda': n_solicitacoes_agenda,
                    'perfis': n_perfis,
                    'reservas': n_reservas,
                },
                'mensagem': msg,
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {'error': f'Ocorreu um erro durante a fusão: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
class UnificarMunicipesPreviewView(APIView):
    """Simula unificação sem persistir (dry-run)."""
    permission_classes = [permissions.IsAuthenticated, CanAccessContactsAvancado]

    def post(self, request):
        id_principal = request.data.get('id_principal')
        id_duplicado = request.data.get('id_duplicado')

        if not id_principal or not id_duplicado:
            return Response({'detail': 'IDs inválidos.'}, status=status.HTTP_400_BAD_REQUEST)

        if str(id_principal) == str(id_duplicado):
            return Response({'detail': 'Você não pode unificar um registro com ele mesmo.'}, status=400)

        principal = get_object_or_404(Municipe, pk=id_principal)
        duplicado = get_object_or_404(Municipe, pk=id_duplicado)

        preview = preview_unificacao_municipes(principal, duplicado)
        return Response(preview)


class UnificarMunicipesView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanAccessContactsAvancado]

    def post(self, request):
        logger = logging.getLogger(__name__)
        id_principal = request.data.get('id_principal')
        id_duplicado = request.data.get('id_duplicado')

        if not id_principal or not id_duplicado:
            return Response({"detail": "IDs inválidos."}, status=400)

        if str(id_principal) == str(id_duplicado):
            return Response({"detail": "Você não pode unificar um registro com ele mesmo."}, status=400)

        try:
            logger.info(f"Iniciando unificação: principal={id_principal}, duplicado={id_duplicado}")
            
            # Inicializar lista para objetos que serão deletados após commit
            objetos_para_deletar_apos_commit = []
            
            # Buscar objetos ANTES da transação para evitar problemas
            principal = get_object_or_404(Municipe, pk=id_principal)
            duplicado = get_object_or_404(Municipe, pk=id_duplicado)
            logger.info(f"Objetos carregados: principal={principal.nome_completo} (ID: {principal.id}), duplicado={duplicado.nome_completo} (ID: {duplicado.id})")
            
            duplicado_id = duplicado.id
            duplicado_nome = duplicado.nome_completo
            links_migrados = 0

            with suppress_crm_logs():
                with transaction.atomic():

                    # 1. COPIA DADOS FALTANTES (Merge de Informações)
                    if not principal.cpf and duplicado.cpf:
                        principal.cpf = duplicado.cpf
                    if not principal.matricula_rh and duplicado.matricula_rh:
                        principal.matricula_rh = duplicado.matricula_rh
                    if not principal.foto and duplicado.foto:
                        principal.foto = duplicado.foto
                    
                    # Merge inteligente de telefones
                    if duplicado.telefones:
                        if not principal.telefones: principal.telefones = []
                        numeros_existentes = [t.get('numero') for t in principal.telefones if t.get('numero')]
                        for tel in duplicado.telefones:
                            if tel.get('numero') and tel.get('numero') not in numeros_existentes:
                                principal.telefones.append(tel)

                    # Merge inteligente de emails
                    if duplicado.emails:
                        if not principal.emails: principal.emails = []
                        emails_existentes = [e.get('email') for e in principal.emails if e.get('email')]
                        for mail in duplicado.emails:
                            if mail.get('email') and mail.get('email') not in emails_existentes:
                                principal.emails.append(mail)

                    logger.info(f"Salvando principal (ID: {principal.id})")
                    principal.save(update_fields=['cpf', 'matricula_rh', 'foto', 'telefones', 'emails'])
                    logger.info(f"Principal salvo com sucesso")

                    # 2. TRANSFERIR VÍNCULOS (CORREÇÃO DA INTROSPECÇÃO)
                
                    # get_fields() traz todas as relações. Filtramos as que apontam PARA Munícipe (one_to_many)
                    logger.info("Iniciando transferência de vínculos one-to-many")
                    for rel in Municipe._meta.get_fields():
                        
                        # Verifica se é uma relação reversa (OutroModel -> Municipe)
                        if rel.one_to_many and rel.auto_created:
                            related_model = rel.related_model
                            remote_field_name = rel.field.name # Ex: 'municipe' ou 'solicitante'

                            # Tratamento ESPECÍFICO para PerfilMunicipe, evitando conflitos de unicidade:
                            # - Se o principal já tiver um perfil com mesmo cargo+conta, descarta o perfil do duplicado.
                            # - Caso contrário, transfere o perfil alterando o municipe_id.
                            if related_model.__name__ == 'PerfilMunicipe':
                                try:
                                    objetos = list(related_model.objects.filter(**{remote_field_name: duplicado}))
                                    logger.debug(f"Processando {len(objetos)} perfis de PerfilMunicipe para migrar")
                                    for perfil in objetos:
                                        existe = related_model.objects.filter(
                                            municipe=principal,
                                            conta=perfil.conta,
                                            cargo=perfil.cargo,
                                        ).exists()
                                        if existe:
                                            logger.debug(
                                                "Descartando PerfilMunicipe duplicado (principal já possui mesmo cargo/conta): "
                                                f"perfil_id={perfil.id}, conta_id={perfil.conta_id}, cargo={perfil.cargo}"
                                            )
                                            perfil.delete()
                                        else:
                                            perfil.municipe = principal
                                            perfil.save()
                                            links_migrados += 1
                                except Exception as e:
                                    logger.error(f"Erro ao migrar perfis de PerfilMunicipe: {e}", exc_info=True)
                                # Já tratamos PerfilMunicipe, segue para a próxima relação
                                continue
                        
                            # Busca objetos do modelo relacionado que apontam para o duplicado
                            try:
                                logger.debug(f"Processando relação: {related_model.__name__}.{remote_field_name}")
                                filtro = {remote_field_name: duplicado}
                                # IMPORTANTE: Converter para lista ANTES de entrar no loop para evitar lazy evaluation
                                objetos = list(related_model.objects.filter(**filtro))
                                logger.debug(f"Encontrados {len(objetos)} objetos para migrar em {related_model.__name__}")
                            
                                for idx, obj in enumerate(objetos):
                                    obj_id = obj.pk  # Captura ID antes de qualquer modificação
                                    try:
                                        # ENVELOPE ESTA AÇÃO COM ATOMIC:
                                        # O atomic() aninhado cria um savepoint automaticamente
                                        # Se der erro, faz rollback apenas desta operação, mantendo a transação principal intacta
                                        with transaction.atomic():
                                            setattr(obj, remote_field_name, principal)
                                            obj.save()
                                        links_migrados += 1
                                        logger.debug(f"Migrado objeto {idx+1}/{len(objetos)} de {related_model.__name__} (ID: {obj_id})")
                                    except IntegrityError as ie:
                                        # O rollback aconteceu APENAS no savepoint interno.
                                        # A transação principal continua intacta e o delete() vai funcionar!
                                        logger.warning(f"IntegrityError ao migrar {related_model.__name__} ID {obj_id}: {ie}. Removendo duplicado.")
                                    
                                        # Reverter mudança em memória antes de deletar
                                        setattr(obj, remote_field_name, duplicado)
                                        try:
                                            obj.delete()
                                            links_migrados += 1  # Conta como migrado (foi removido)
                                            logger.debug(f"Duplicado removido: {related_model.__name__} ID {obj_id}")
                                        except Exception as delete_err:
                                            logger.error(f"Erro ao deletar objeto duplicado {related_model.__name__} ID {obj_id}: {delete_err}")
                                            # Não re-raise aqui, apenas loga e continua
                                            # O objeto pode ser removido manualmente depois
                                    except Exception as obj_error:
                                        logger.error(f"Erro ao processar objeto {idx+1} de {related_model.__name__} (ID: {obj_id}): {obj_error}", exc_info=True)
                                        # Reverte mudança em memória antes de continuar
                                        try:
                                            setattr(obj, remote_field_name, duplicado)
                                        except:
                                            pass  # Se não conseguir reverter, continua mesmo assim
                                        # NÃO re-raise aqui - apenas loga e continua para não quebrar a transação
                                        # O objeto problemático será deixado apontando para o duplicado
                                        # e pode ser corrigido manualmente depois
                            except Exception as e:
                                # Log do erro mas continua processamento
                                logger.error(f"Erro ao migrar relação {related_model.__name__}.{remote_field_name}: {e}", exc_info=True)
                                continue

                    # 3. MIGRAR RELAÇÕES MANY-TO-MANY (Ex: Listas de Distribuição)
                    # O loop acima (one_to_many) não pega ManyToMany.
                    logger.info("Iniciando transferência de vínculos many-to-many")
                    for rel in Municipe._meta.get_fields():
                        if rel.many_to_many and rel.auto_created:
                            related_model = rel.related_model
                            remote_field_name = rel.field.name 
                        
                            # Para M2M, o filtro é um pouco diferente
                            try:
                                logger.debug(f"Processando relação M2M: {related_model.__name__}.{remote_field_name}")
                                filtro = {remote_field_name: duplicado}
                            
                                # Usar atomic() aninhado para isolar cada relação M2M (cria savepoint automaticamente)
                                with transaction.atomic():
                                    try:
                                        objetos_m2m = list(related_model.objects.filter(**filtro))
                                        logger.debug(f"Encontrados {len(objetos_m2m)} objetos M2M para migrar em {related_model.__name__}")
                                    except Exception as query_error:
                                        logger.error(f"Erro ao buscar objetos M2M de {related_model.__name__}: {query_error}", exc_info=True)
                                        continue  # Pula esta relação e continua com a próxima
                                
                                    for idx, obj in enumerate(objetos_m2m):
                                        obj_id = obj.pk  # Captura ID antes de qualquer modificação
                                        try:
                                            # Usar atomic() aninhado para cada objeto M2M (cria savepoint automaticamente)
                                            with transaction.atomic():
                                                # Pega o manager do campo M2M no objeto relacionado
                                                m2m_manager = getattr(obj, remote_field_name)
                                                m2m_manager.remove(duplicado)
                                                m2m_manager.add(principal)
                                            links_migrados += 1
                                            logger.debug(f"Migrado objeto M2M {idx+1}/{len(objetos_m2m)} de {related_model.__name__} (ID: {obj_id})")
                                        except Exception as obj_error:
                                            logger.error(f"Erro ao processar objeto M2M {idx+1} de {related_model.__name__} (ID: {obj_id}): {obj_error}", exc_info=True)
                                            # NÃO re-raise aqui - apenas loga e continua para não quebrar a transação
                                            # O objeto problemático pode ser corrigido manualmente depois
                            except Exception as e:
                                # Log do erro mas continua processamento - NUNCA re-raise aqui!
                                logger.error(f"Erro ao migrar relação M2M {related_model.__name__}.{remote_field_name}: {e}", exc_info=True)
                                continue

                    # 4. EXCLUIR OBJETOS DUPLICADOS E O DUPLICADO (APÓS COMMIT DA TRANSAÇÃO)
                    logger.info(f"Preparando exclusão de {len(objetos_para_deletar_apos_commit)} objetos duplicados e munícipe duplicado após commit")
                
                    # Usar on_commit para deletar APÓS a transação ser commitada com sucesso
                    def deletar_objetos_apos_commit():
                        with suppress_crm_logs():
                            for model_class, obj_id in objetos_para_deletar_apos_commit:
                                try:
                                    obj = model_class.objects.filter(pk=obj_id).first()
                                    if obj:
                                        obj.delete()
                                        logger.info(f"Objeto duplicado deletado após commit: {model_class.__name__} ID {obj_id}")
                                except Exception as delete_error:
                                    logger.error(f"Erro ao deletar objeto duplicado {model_class.__name__} ID {obj_id} após commit: {delete_error}", exc_info=True)

                            try:
                                duplicado_refresh = Municipe.objects.filter(pk=duplicado_id).first()
                                if duplicado_refresh:
                                    logger.info(f"Deletando duplicado após commit (ID: {duplicado_id})")
                                    duplicado_refresh.delete()
                                    logger.info(f"Duplicado excluído com sucesso após commit (ID: {duplicado_id})")
                                else:
                                    logger.warning(f"Duplicado já não existe (ID: {duplicado_id})")
                            except Exception as delete_error:
                                logger.error(f"Erro ao deletar duplicado após commit (ID: {duplicado_id}): {delete_error}", exc_info=True)
                                logger.warning(f"Duplicado não foi deletado automaticamente. ID: {duplicado_id}, Nome: {duplicado_nome}")

                    transaction.on_commit(deletar_objetos_apos_commit)

                    logger.info(f"Unificação concluída: {links_migrados} vínculos migrados. Duplicado será deletado após commit.")

            registrar_log_mesclagem_municipes(
                request,
                principal,
                duplicado_id,
                duplicado_nome,
                links_migrados=links_migrados,
            )

            return Response({
                "message": "Fusão concluída com sucesso.",
                "links_migrados": links_migrados,
                "nome_final": principal.nome_completo,
                "duplicado_id": duplicado_id,
                "nota": "O registro duplicado será excluído automaticamente após a conclusão da transação."
            })

        except Exception as e:
            # Log completo do erro para depuração
            logger.error(f"ERRO CRÍTICO ao unificar municipes (principal={id_principal}, duplicado={id_duplicado}): {e}", exc_info=True)
            error_detail = str(e)
            error_type = type(e).__name__
            
            # Mensagem mais detalhada para o cliente
            if "atomic" in error_detail.lower() or "transaction" in error_detail.lower():
                error_detail = f"Erro de transação: {error_detail}. Verifique os logs do servidor para mais detalhes."
            
            return Response({
                "detail": f"Erro ao unificar: {error_detail}",
                "error_type": error_type,
                "principal_id": id_principal,
                "duplicado_id": id_duplicado
            }, status=500)


class RodarAuditoriaDuplicidadesView(APIView):
    """Dispara o management command auditoria_qualidade_duplicidades em background. Retorna 202 Accepted."""
    permission_classes = [permissions.IsAuthenticated, CanAccessContactsAvancado]

    def post(self, request):
        def run_command():
            try:
                call_command('auditoria_qualidade_duplicidades')
            except Exception as e:
                logger.exception('Erro ao rodar auditoria_qualidade_duplicidades: %s', e)

        threading.Thread(target=run_command, daemon=True).start()
        return Response(
            {'message': 'Auditoria de qualidade e duplicidades iniciada em background. A lista será atualizada em alguns minutos.'},
            status=status.HTTP_202_ACCEPTED
        )


class DuplicatasContadorView(APIView):
    """Total de grupos pendentes de duplicatas (2+ contatos no mesmo grupo)."""
    permission_classes = [permissions.IsAuthenticated, CanAccessContactsAvancado]

    def get(self, request):
        from django.db.models import Count
        from .services.escopo_operador_crm import aplicar_escopo_municipes_queryset

        qs = Municipe.objects.exclude(grupo_duplicado__isnull=True)
        qs = aplicar_escopo_municipes_queryset(qs, request.user)

        grupos = list(
            qs.values('grupo_duplicado')
            .annotate(total=Count('pk', distinct=True))
            .filter(total__gte=2)
        )
        total_grupos = len(grupos)
        total_contatos = sum(item['total'] for item in grupos)

        return Response({
            'total_grupos': total_grupos,
            'total_contatos': total_contatos,
        })


class DescartarGrupoDuplicatasView(APIView):
    """Limpa grupo_duplicado de todos os munícipes do grupo informado (UUID)."""
    permission_classes = [permissions.IsAuthenticated, CanAccessContactsAvancado]

    def post(self, request):
        grupo_uuid = request.data.get('grupo_duplicado')
        if not grupo_uuid:
            return Response({'error': 'grupo_duplicado (UUID) é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            uuid.UUID(str(grupo_uuid))
        except ValueError:
            return Response({'error': 'grupo_duplicado inválido.'}, status=status.HTTP_400_BAD_REQUEST)

        atualizados = Municipe.objects.filter(grupo_duplicado=grupo_uuid).update(grupo_duplicado=None)
        return Response({'message': f'Grupo ignorado. {atualizados} contato(s) removido(s) do grupo.', 'atualizados': atualizados})


class DescartarContatoDuplicataView(APIView):
    """Remove um único munícipe do grupo de duplicatas (limpa grupo_duplicado desse ID)."""
    permission_classes = [permissions.IsAuthenticated, CanAccessContactsAvancado]

    def post(self, request, pk):
        municipe = get_object_or_404(Municipe, pk=pk)
        if not municipe.grupo_duplicado:
            return Response({'message': 'Contato já não está em grupo de duplicatas.', 'atualizados': 0})
        municipe.grupo_duplicado = None
        municipe.save(update_fields=['grupo_duplicado'])
        return Response({'message': 'Contato removido do grupo de duplicatas.', 'atualizados': 1})


def _formatar_cargo_orgao_municipe(m):
    """Formata cargo(s)/órgão(s) do munícipe a partir de perfis ou campos legados."""
    perfis_list = list(m.perfis.all()) if hasattr(m, 'perfis') else []
    if perfis_list:
        partes = []
        for p in perfis_list:
            if getattr(p, 'ativo', True):
                cargo = (p.cargo or '').strip()
                inst = (p.instituicao or '').strip()
                if cargo and inst:
                    partes.append(f"{cargo} @ {inst}")
                elif cargo:
                    partes.append(cargo)
                elif inst:
                    partes.append(inst)
        if partes:
            return '; '.join(partes)
    c = (m.cargo or '').strip()
    o = (m.orgao or '').strip()
    if c and o:
        return f"{c} @ {o}"
    return c or o or ''


class SaneamentoDadosMunicipeView(APIView):
    """
    Retorna uma lista de problemas de qualidade de dados para munícipes,
    permitindo filtros por tipo de problema e busca geral.
    Problemas suportados:
      - telefone_invalido
      - email_invalido
      - cpf_ausente
    """
    permission_classes = [permissions.IsAuthenticated, CanAccessContactsAvancado]

    def get(self, request):
        problemas_param = request.query_params.getlist('problema') or ['telefone_invalido', 'email_invalido', 'cpf_ausente']
        problemas = set(problemas_param)
        q_busca = (request.query_params.get('q') or '').strip()

        resultados = []
        qs = Municipe.objects.prefetch_related('perfis').only(
            'id', 'nome_completo', 'cpf', 'telefones', 'emails', 'auditoria_ia', 'cargo', 'orgao'
        )

        if q_busca:
            from .services.busca_textual import filtrar_queryset_municipe

            qs = filtrar_queryset_municipe(qs, q_busca).distinct()

        for m in qs.iterator(chunk_size=500):
            # Telefones inválidos (validação via is_data_dirty)
            if 'telefone_invalido' in problemas and m.telefones and isinstance(m.telefones, list):
                for tel in m.telefones:
                    if isinstance(tel, dict) and tel.get('numero'):
                        num = str(tel['numero'])
                        if is_data_dirty(num, 'telefone'):
                            resultados.append({
                                'id': m.id,
                                'nome_completo': m.nome_completo,
                                'cargo_orgao': _formatar_cargo_orgao_municipe(m),
                                'problema': 'telefone_invalido',
                                'campo': 'telefones',
                                'valor_atual': num,
                            })
                            break

            # Emails inválidos (validação via is_data_dirty)
            if 'email_invalido' in problemas and m.emails and isinstance(m.emails, list):
                for em in m.emails:
                    if isinstance(em, dict) and em.get('email'):
                        addr = str(em['email'])
                        if is_data_dirty(addr, 'email'):
                            resultados.append({
                                'id': m.id,
                                'nome_completo': m.nome_completo,
                                'cargo_orgao': _formatar_cargo_orgao_municipe(m),
                                'problema': 'email_invalido',
                                'campo': 'emails',
                                'valor_atual': addr,
                            })
                            break

            # CPF ausente
            if 'cpf_ausente' in problemas:
                cpf = (m.cpf or '').strip() if m.cpf else ''
                if not cpf:
                    resultados.append({
                        'id': m.id,
                        'nome_completo': m.nome_completo,
                        'cargo_orgao': _formatar_cargo_orgao_municipe(m),
                        'problema': 'cpf_ausente',
                        'campo': 'cpf',
                        'valor_atual': '',
                    })

        return Response(resultados)


class AtualizarCategoriaEmLoteView(APIView):
    """
    View para atualizar a categoria de múltiplos perfis de munícipes.
    Recebe perfil_ids e nova_categoria_id.
    """
    permission_classes = [permissions.IsAuthenticated, CanAccessContactsAvancado]

    def post(self, request, *args, **kwargs):
        from .models import PerfilMunicipe

        perfil_ids = request.data.get('perfil_ids', [])
        nova_categoria_id = request.data.get('nova_categoria_id', None)

        if not isinstance(perfil_ids, list) or not perfil_ids:
            return Response(
                {'error': 'Lista de IDs de perfis inválida ou vazia.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if nova_categoria_id is None:
            return Response(
                {'error': 'ID da nova categoria é obrigatório.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            nova_categoria = CategoriaContato.objects.get(pk=nova_categoria_id)
        except CategoriaContato.DoesNotExist:
            return Response(
                {'error': f'Categoria com ID {nova_categoria_id} não encontrada.'},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            user = request.user
            qs = PerfilMunicipe.objects.filter(id__in=perfil_ids)
            if not user.is_superuser and hasattr(user, 'perfil'):
                contas_usuario = user.perfil.contas.all()
                qs = qs.filter(conta__in=contas_usuario)
            num_atualizados = qs.update(categoria=nova_categoria)
            return Response(
                {'message': f'{num_atualizados} perfil(is) atualizado(s) com sucesso para a categoria "{nova_categoria.nome}".'},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response(
                {'error': f'Ocorreu um erro ao atualizar: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

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

        # Tratamento robusto para booleano vindo do request (frontend as vezes manda string "true")
        notificar_raw = self.request.data.get('notificar_municipe', False)
        notificar = str(notificar_raw).lower() == 'true' if isinstance(notificar_raw, str) else bool(notificar_raw)

        # Pega o e-mail
        municipe_email_principal = None
        if atendimento_instance.municipe and atendimento_instance.municipe.emails:
            municipe_email_principal = atendimento_instance.municipe.emails[0].get('email')

        if notificar and municipe_email_principal:
            # Prepara apenas os dados textuais. As imagens o utils resolve.
            contexto = {
                'nome_municipe': atendimento_instance.municipe.nome_completo,
                'protocolo': atendimento_instance.protocolo,
                'titulo': atendimento_instance.titulo,
                'despacho': tramitacao.despacho,
            }

            # CHAMA O UTILITÁRIO (Código limpo e seguro)
            enviar_email_com_cid(
                assunto=f"Atualização do seu Atendimento - Protocolo: {atendimento_instance.protocolo}",
                destinatarios=[municipe_email_principal],
                template='emails/notificacao_tramitacao.html',
                contexto=contexto,
                conta=atendimento_instance.conta
            )

class TramitacaoDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Tramitacao.objects.all()
    serializer_class = TramitacaoSerializer
    permission_classes = [permissions.IsAuthenticated]


class AlterarStatusAtendimentoView(generics.GenericAPIView):
    """
    Endpoint específico para alterar status do atendimento via tramitação.
    Requer despacho obrigatório e dados de encaminhamento (se status=ENCAMINHADO).
    
    Payload esperado:
    {
        "status_novo": "ENCAMINHADO",
        "despacho": "Encaminhado para Secretaria de Educação...",
        "encaminhado_para_sinapse_id": 123,  # Obrigatório se status=ENCAMINHADO
        "encaminhado_para_nome": "Secretaria de Educação",  # Opcional
        "encaminhado_para_tipo": "Secretaria",  # Opcional
        "notificar_municipe": true  # Opcional
    }
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request, pk):
        from .models import Atendimento, Tramitacao
        from .validators import validar_transicao_status, validar_encaminhamento
        from .permissions import CanInteractWithAtendimento
        from .utils import enviar_email_com_cid
        
        atendimento = get_object_or_404(Atendimento, pk=pk)
        
        # Valida permissão
        permission_check = CanInteractWithAtendimento()
        if not permission_check.has_object_permission(request, self, atendimento):
            return Response(
                {'detail': 'Você não tem permissão para alterar este atendimento.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Valida dados obrigatórios
        status_novo = request.data.get('status_novo')
        despacho = request.data.get('despacho')
        
        if not status_novo:
            return Response(
                {'detail': 'Campo "status_novo" é obrigatório.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not despacho or not despacho.strip():
            return Response(
                {'detail': 'Campo "despacho" é obrigatório para alterar o status.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Valida transição de status
        try:
            validar_transicao_status(atendimento.status, status_novo)
        except ValidationError as e:
            return Response(
                {'detail': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Valida encaminhamento se necessário
        dados_encaminhamento = {
            'encaminhado_para_sinapse_id': request.data.get('encaminhado_para_sinapse_id'),
            'encaminhado_para_nome': request.data.get('encaminhado_para_nome'),
            'encaminhado_para_tipo': request.data.get('encaminhado_para_tipo'),
        }
        
        try:
            validar_encaminhamento(status_novo, dados_encaminhamento)
        except ValidationError as e:
            return Response(
                {'detail': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Cria tramitação com mudança de status
        try:
            with transaction.atomic():
                tramitacao = Tramitacao.objects.create(
                    atendimento=atendimento,
                    despacho=despacho.upper(),
                    usuario=request.user,
                    status_anterior=atendimento.status,
                    status_novo=status_novo,
                    alterou_status=True,
                    encaminhado_para_sinapse_id=dados_encaminhamento.get('encaminhado_para_sinapse_id'),
                    encaminhado_para_nome=dados_encaminhamento.get('encaminhado_para_nome'),
                    encaminhado_para_tipo=dados_encaminhamento.get('encaminhado_para_tipo'),
                )
                
                # Atualiza status do atendimento
                atendimento.status = status_novo
                atendimento.save()
                
                # Notifica munícipe se solicitado
                notificar_raw = request.data.get('notificar_municipe', False)
                notificar = str(notificar_raw).lower() == 'true' if isinstance(notificar_raw, str) else bool(notificar_raw)
                
                if notificar and atendimento.municipe and atendimento.municipe.emails:
                    municipe_email_principal = atendimento.municipe.emails[0].get('email')
                    if municipe_email_principal:
                        contexto = {
                            'nome_municipe': atendimento.municipe.nome_completo,
                            'protocolo': atendimento.protocolo,
                            'titulo': atendimento.titulo,
                            'despacho': tramitacao.despacho,
                            'status_novo': atendimento.get_status_display(),
                        }
                        
                        enviar_email_com_cid(
                            assunto=f"Atualização do seu Atendimento - Protocolo: {atendimento.protocolo}",
                            destinatarios=[municipe_email_principal],
                            template='emails/notificacao_tramitacao.html',
                            contexto=contexto,
                            conta=atendimento.conta
                        )
                
                serializer = TramitacaoSerializer(tramitacao)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
                
        except Exception as e:
            logger.error(f"Erro ao alterar status do atendimento {pk}: {str(e)}")
            return Response(
                {'detail': f'Erro ao alterar status: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RedirecionarAtendimentoView(APIView):
    """
    Redireciona o atendimento para um novo responsável (usuário da mesma conta).
    Gera tramitação com justificativa e notifica o novo responsável.
    POST: { novo_responsavel_id: int, justificativa: str }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        atendimento = get_object_or_404(Atendimento, pk=pk)
        permission_check = CanInteractWithAtendimento()
        if not permission_check.has_object_permission(request, self, atendimento):
            return Response({'detail': 'Sem permissão.'}, status=status.HTTP_403_FORBIDDEN)

        novo_id = request.data.get('novo_responsavel_id')
        justificativa = (request.data.get('justificativa') or '').strip()
        if not novo_id:
            return Response({'detail': 'novo_responsavel_id é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)
        if not justificativa:
            return Response({'detail': 'justificativa é obrigatória.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            novo_responsavel = User.objects.get(pk=novo_id, is_active=True)
        except User.DoesNotExist:
            return Response({'detail': 'Usuário não encontrado.'}, status=status.HTTP_400_BAD_REQUEST)

        if not hasattr(novo_responsavel, 'perfil') or not novo_responsavel.perfil.contas.filter(pk=atendimento.conta_id).exists():
            return Response({'detail': 'O usuário deve pertencer à mesma conta do atendimento.'}, status=status.HTTP_400_BAD_REQUEST)

        if novo_responsavel == atendimento.responsavel:
            return Response({'detail': 'O atendimento já está sob responsabilidade deste usuário.'}, status=status.HTTP_400_BAD_REQUEST)

        antigo = atendimento.responsavel
        nome_antigo = (antigo.get_full_name() or antigo.username) if antigo else 'Ninguém'
        nome_novo = novo_responsavel.get_full_name() or novo_responsavel.username

        with transaction.atomic():
            atendimento.responsavel = novo_responsavel
            atendimento.responsaveis_compartilhados.clear()
            atendimento.save(update_fields=['responsavel'])

            despacho = f"[REDIRECIONAMENTO] Atendimento redirecionado de {nome_antigo} para {nome_novo}.\n\nJustificativa: {justificativa}"
            Tramitacao.objects.create(
                atendimento=atendimento,
                despacho=despacho,
                usuario=request.user
            )

            Notificacao.objects.create(
                usuario=novo_responsavel,
                mensagem=f"O atendimento {atendimento.protocolo} foi atribuído a você. {atendimento.titulo[:50]}...",
                link=f"/atendimentos/{atendimento.pk}"
            )

        data = AtendimentoSerializer(atendimento).data
        return Response(data, status=status.HTTP_200_OK)


class CompartilharAtendimentoView(APIView):
    """
    Compartilha o atendimento com outro membro da equipe (mesma conta).
    Ambos permanecem responsáveis. Gera tramitação e notifica o novo co-responsável.
    POST: { usuario_id: int, justificativa: str }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        atendimento = get_object_or_404(Atendimento, pk=pk)
        permission_check = CanInteractWithAtendimento()
        if not permission_check.has_object_permission(request, self, atendimento):
            return Response({'detail': 'Sem permissão.'}, status=status.HTTP_403_FORBIDDEN)

        usuario_id = request.data.get('usuario_id')
        justificativa = (request.data.get('justificativa') or '').strip()
        if not usuario_id:
            return Response({'detail': 'usuario_id é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)
        if not justificativa:
            return Response({'detail': 'justificativa é obrigatória.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            novo_usuario = User.objects.get(pk=usuario_id, is_active=True)
        except User.DoesNotExist:
            return Response({'detail': 'Usuário não encontrado.'}, status=status.HTTP_400_BAD_REQUEST)

        if not hasattr(novo_usuario, 'perfil') or not novo_usuario.perfil.contas.filter(pk=atendimento.conta_id).exists():
            return Response({'detail': 'O usuário deve pertencer à mesma conta do atendimento.'}, status=status.HTTP_400_BAD_REQUEST)

        if atendimento.responsaveis_compartilhados.filter(pk=novo_usuario.pk).exists():
            return Response({'detail': 'O atendimento já está compartilhado com este usuário.'}, status=status.HTTP_400_BAD_REQUEST)

        if atendimento.responsavel_id == novo_usuario.pk:
            return Response({'detail': 'O usuário já é o responsável principal.'}, status=status.HTTP_400_BAD_REQUEST)

        nome_novo = novo_usuario.get_full_name() or novo_usuario.username

        with transaction.atomic():
            atendimento.responsaveis_compartilhados.add(novo_usuario)

            despacho = f"[COMPARTILHAMENTO] Atendimento compartilhado com {nome_novo}. Ambos podem gerir.\n\nJustificativa: {justificativa}"
            Tramitacao.objects.create(
                atendimento=atendimento,
                despacho=despacho,
                usuario=request.user
            )

            Notificacao.objects.create(
                usuario=novo_usuario,
                mensagem=f"O atendimento {atendimento.protocolo} foi compartilhado com você. {atendimento.titulo[:50]}...",
                link=f"/atendimentos/{atendimento.pk}"
            )

        data = AtendimentoSerializer(atendimento).data
        return Response(data, status=status.HTTP_200_OK)


class BuscarSecretariasSinapseView(generics.GenericAPIView):
    """
    Endpoint para buscar secretarias/órgãos da API Sinapse.
    Usado no frontend para preencher dropdown de encaminhamento.
    
    Retorna lista de secretarias ativas da API Sinapse ou do cache local.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        from django.db import connection
        from .services.sinapse_api import buscar_estrutura_organizacional, SinapseAPIError
        
        # Verifica se a tabela SinapseSecretaria existe (migração foi executada)
        tabela_existe = False
        try:
            from .models import SinapseSecretaria
            # Tenta fazer uma query simples para verificar se a tabela existe
            SinapseSecretaria.objects.exists()
            tabela_existe = True
        except Exception as e:
            logger.debug(f"Tabela SinapseSecretaria não existe ou migração não executada: {str(e)}")
            tabela_existe = False
        
        # Se a tabela existe, tenta buscar do cache local primeiro
        if tabela_existe:
            try:
                from .models import SinapseSecretaria
                secretarias_cache = SinapseSecretaria.objects.filter(ativo=True).order_by('nome')
                
                if secretarias_cache.exists():
                    # Retorna do cache
                    data = [
                        {
                            'id': s.sinapse_id,
                            'nome': s.nome,
                            'sigla': s.sigla or '',
                            'tipo': s.tipo,
                            'hierarquia': s.hierarquia,
                        }
                        for s in secretarias_cache
                    ]
                    return Response(data, status=status.HTTP_200_OK)
            except Exception as e:
                logger.warning(f"Erro ao buscar cache local de secretarias: {str(e)}")
                # Continua para tentar buscar da API
        
        # Se não tem cache ou tabela não existe, tenta buscar da API
        logger.info("Iniciando busca de secretarias da API Sinapse...")
        try:
            estrutura = buscar_estrutura_organizacional()
            logger.info(f"API Sinapse retornou {len(estrutura) if estrutura else 0} itens")
            
            # Normaliza resposta da API conforme estrutura real: /api/v1/unidades/
            if estrutura:
                data = []
                for item in estrutura:
                    # Mapeia campos da API Sinapse para o formato esperado pelo frontend
                    data.append({
                        'id': item.get('id'),
                        'nome': item.get('nome') or item.get('nome_reduzido', ''),
                        'sigla': item.get('sigla') or '',
                        'tipo': item.get('tipo_unidade') or 'Secretaria',
                        'hierarquia': {
                            'codigo_hierarquico': item.get('codigo_hierarquico'),
                            'nivel_hierarquico': item.get('nivel_hierarquico'),
                            'unidade_pai': item.get('unidade_pai'),
                            'nome_pai': item.get('nome_pai'),
                        } if item.get('codigo_hierarquico') else None,
                    })
                
                return Response(data, status=status.HTTP_200_OK)
            else:
                # Retorna lista vazia se API não retornar dados
                return Response([], status=status.HTTP_200_OK)
                
        except SinapseAPIError as e:
            logger.warning(f"Erro ao buscar secretarias da API Sinapse: {str(e)}")
            # Em DEBUG, retorna erro detalhado. Em produção, retorna lista vazia
            if settings.DEBUG:
                return Response(
                    {
                        'detail': str(e),
                        'error_type': 'SinapseAPIError',
                        'message': 'Verifique se SINAPSE_API_TOKEN está configurado no .env e se o endpoint está correto'
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE
                )
            return Response([], status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Erro inesperado ao buscar secretarias: {str(e)}", exc_info=True)
            # Retorna erro detalhado em desenvolvimento, lista vazia em produção
            if settings.DEBUG:
                return Response(
                    {
                        'detail': str(e),
                        'error_type': type(e).__name__,
                        'traceback': traceback.format_exc() if settings.DEBUG else None
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            return Response([], status=status.HTTP_200_OK)


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
    SAFE_PERMISSION_CLAIMS = {
        "eventos.pode_gerenciar_eventos",
        "oficios.pode_gerenciar_oficios",
    }

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Adiciona dados customizados ao token
        token['username'] = user.username
        token['is_superuser'] = user.is_superuser
        token['groups'] = list(user.groups.values_list('name', flat=True))
        # Evita JWT gigante para superadmin (causava 400 em requests por header excessivo).
        # O frontend usa apenas permissões específicas para feature flags.
        token['user_permissions'] = sorted(
            perm for perm in user.get_all_permissions() if perm in cls.SAFE_PERMISSION_CLAIMS
        )

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
        from .reporting import queryset_atendimentos_relatorio

        queryset = queryset_atendimentos_relatorio(request.user, request)
        data = queryset.values('status').annotate(total=Count('id')).order_by('status')
        return Response(data)


class RelatorioAtendimentosPorContaView(APIView):
    permission_classes = [IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        from .reporting import queryset_atendimentos_relatorio

        queryset = queryset_atendimentos_relatorio(request.user, request)
        data = queryset.values('conta__nome').annotate(total=Count('id')).order_by('-total')
        return Response(data)


class RelatorioFiltrosPerfilView(APIView):
    """Opções de categoria de contato e cargo para filtros de relatório."""
    permission_classes = [IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        from .reporting import opcoes_cargos_relatorio, opcoes_categorias_relatorio

        return Response({
            'categorias': opcoes_categorias_relatorio(request.user),
            'cargos': opcoes_cargos_relatorio(request.user),
        })


class RelatorioAtendimentosPorCategoriaView(APIView):
    """Deprecated (Fase 8): delega agregação por assunto; mantém formato legado."""

    permission_classes = [IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        from .reporting import queryset_atendimentos_relatorio, serializar_atendimentos_por_assunto

        queryset = queryset_atendimentos_relatorio(request.user, request)
        por_assunto = serializar_atendimentos_por_assunto(queryset)
        data = [
            {'categorias__nome': row['nome'], 'total': row['total']}
            for row in por_assunto
        ]
        response = Response(data)
        response['Deprecation'] = 'true'
        response['Link'] = '</api/relatorios/atendimentos-por-assunto/>; rel="successor-version"'
        return response


class RelatorioAtendimentosPorAssuntoView(APIView):
    permission_classes = [IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        from .reporting import queryset_atendimentos_relatorio, serializar_atendimentos_por_assunto

        queryset = queryset_atendimentos_relatorio(request.user, request)
        return Response(serializar_atendimentos_por_assunto(queryset))


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
            if hasattr(user, 'perfil'):
                data['atendimentos_do_dia'] = Atendimento.objects.filter(
                    conta__in=user.perfil.contas.all(),
                    data_criacao__range=(inicio_do_dia, fim_do_dia),
                ).count()

        if hasattr(user, 'perfil'):
            from .reporting import big_numbers_por_assunto

            qs_hoje = Atendimento.objects.filter(
                conta__in=user.perfil.contas.all(),
                data_criacao__range=(inicio_do_dia, fim_do_dia),
            )
            if not (user.is_superuser or is_in_group(user, 'Recepção')):
                qs_hoje = qs_hoje.filter(
                    Q(responsavel=user) | Q(responsavel__isnull=True)
                )
            data['atendimentos_por_assunto_hoje'] = big_numbers_por_assunto(qs_hoje, top=5)

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

            from .services.sla_atendimento import filtrar_queryset_por_sla

            qs_sla = Atendimento.objects.filter(conta__in=user.perfil.contas.all())
            data['sla_vencidos'] = filtrar_queryset_por_sla(qs_sla, 'VENCIDO').count()
            data['sla_em_risco'] = filtrar_queryset_por_sla(qs_sla, 'EM_RISCO').count()

        return Response(data)


class RelatorioSlaAtendimentosView(APIView):
    """Resumo gerencial de cumprimento de SLA (% no prazo por conta e assunto)."""
    permission_classes = [permissions.IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request):
        from .reporting import queryset_atendimentos_relatorio

        queryset = queryset_atendimentos_relatorio(request.user, request)
        return Response(_resposta_sla_atendimentos(queryset))


class BiRelatorioSlaView(APIView):
    """Resumo SLA com os mesmos filtros do painel BI."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .reporting import queryset_bi_atendimentos

        queryset = queryset_bi_atendimentos(request.user, request)
        return Response(_resposta_sla_atendimentos(queryset))


def _resposta_sla_atendimentos(queryset):
    from .services.sla_atendimento import resumo_sla_por_dimensao, resumo_sla_queryset

    return {
        'resumo': resumo_sla_queryset(queryset),
        'por_conta': resumo_sla_por_dimensao(queryset, 'conta'),
        'por_assunto': resumo_sla_por_dimensao(queryset, 'assunto'),
    }


class DashboardVisitasPorDataView(APIView):
    """
    Retorna os registros de visita/check-in para uma data específica.
    Mesma regra de responsabilidade do visitas_hoje (conta + usuario_destino/registrado_por/null).
    Query param: data (YYYY-MM-DD). Se ausente, usa hoje.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        from django.utils.dateparse import parse_date

        user = request.user
        data_str = request.query_params.get('data')
        if data_str:
            try:
                data_obj = parse_date(data_str)
                if not data_obj:
                    return Response({'detail': 'Data inválida. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
            except (ValueError, TypeError):
                return Response({'detail': 'Data inválida.'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            data_obj = timezone.localdate()

        contas_usuario = user.perfil.contas.all() if hasattr(user, 'perfil') else Conta.objects.none()
        if user.is_superuser and not contas_usuario.exists():
            contas_usuario = Conta.objects.all()

        if not contas_usuario.exists():
            return Response([])

        from .services.visita_atendimento import listar_visitas_agenda_serializado
        return Response(listar_visitas_agenda_serializado(user, data_obj))


# -----------------------------------------------------------------------------
# Views de Geração de Documentos (PDF, Excel)
# -----------------------------------------------------------------------------

class GerarPdfAtendimentosView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        from .reporting import (
            anotar_linha_cargo_orgao,
            big_numbers_por_assunto,
            big_numbers_por_status,
            queryset_atendimentos_relatorio,
            resumo_filtros_relatorio,
        )

        queryset = queryset_atendimentos_relatorio(request.user, request)
        filtros_resumo = resumo_filtros_relatorio(request)
        conta_id = request.query_params.get('conta_id', None)

        conta_contexto = None
        if conta_id:
            conta_contexto = Conta.objects.filter(id=conta_id).first()
        elif hasattr(request.user, 'perfil') and request.user.perfil.contas.exists():
            conta_contexto = request.user.perfil.contas.first()

        from .reporting import resolver_logos_relatorio_pdf

        logos = resolver_logos_relatorio_pdf(conta_contexto, request)

        big_numbers = big_numbers_por_status(queryset)
        big_numbers_assunto = big_numbers_por_assunto(queryset)
        atendimentos = list(
            queryset.select_related('municipe', 'conta', 'responsavel', 'assunto')
            .prefetch_related('tramitacoes__usuario', 'categorias')
            .order_by('-data_criacao')
        )
        anotar_linha_cargo_orgao(atendimentos, request)

        context = {
            'atendimentos': atendimentos,
            'big_numbers': big_numbers,
            'big_numbers_assunto': big_numbers_assunto,
            'filtros_resumo': filtros_resumo,
            **logos,
        }
        html_string = render_to_string('atendimentos/relatorio_atendimentos.html', context)
        pdf_file = HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf()

        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="relatorio_atendimentos.pdf"'
        return response


class GerarCsvAtendimentosView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        import csv

        from .reporting import (
            anotar_linha_cargo_orgao,
            queryset_atendimentos_relatorio,
            resumo_filtros_relatorio,
        )

        queryset = queryset_atendimentos_relatorio(request.user, request)
        atendimentos = list(
            queryset.select_related('municipe', 'conta', 'responsavel', 'assunto')
            .order_by('-data_criacao')
        )
        anotar_linha_cargo_orgao(atendimentos, request)
        filtros = resumo_filtros_relatorio(request)

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response.write('\ufeff')
        response['Content-Disposition'] = 'attachment; filename="relatorio_atendimentos.csv"'
        writer = csv.writer(response, delimiter=';')

        if filtros.get('tem_filtro_perfil'):
            if filtros.get('categorias'):
                writer.writerow(['Filtro categorias', ', '.join(filtros['categorias'])])
            if filtros.get('cargos'):
                writer.writerow(['Filtro cargos', ', '.join(filtros['cargos'])])
            writer.writerow([])

        writer.writerow([
            'Protocolo', 'Data', 'Título', 'Status', 'Assunto', 'Gabinete',
            'Munícipe', 'Cargo/Órgão', 'Responsável',
        ])
        for at in atendimentos:
            data_str = ''
            if at.data_criacao:
                data_str = timezone.localtime(at.data_criacao).strftime('%d/%m/%Y %H:%M')
            responsavel = ''
            if at.responsavel:
                responsavel = at.responsavel.get_full_name() or at.responsavel.username
            writer.writerow([
                at.protocolo,
                data_str,
                at.titulo,
                at.status,
                getattr(at.assunto, 'nome', '') if at.assunto_id else '',
                getattr(at.conta, 'nome', '') if at.conta_id else '',
                getattr(at.municipe, 'nome_completo', '') if at.municipe_id else '',
                getattr(at, 'linha_cargo_orgao', '—'),
                responsavel,
            ])
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
            brasao_path = None
            logo_conta_path = None
            logo_siga_path = None

            if conta_contexto:
                nome_instituicao = conta_contexto.nome_instituicao or nome_instituicao
                if conta_contexto.brasao_instituicao:
                    # Converte caminho para formato file:// compatível com weasyprint
                    brasao_path = os.path.abspath(conta_contexto.brasao_instituicao.path).replace('\\', '/')
                if conta_contexto.logo_conta:
                    logo_conta_path = os.path.abspath(conta_contexto.logo_conta.path).replace('\\', '/')
            
            # Tenta encontrar o logo do SIGA no sistema de arquivos
            logo_siga_static = settings.STATIC_ROOT / 'images' / 'logo-siga-gab.png'
            if logo_siga_static.exists():
                logo_siga_path = os.path.abspath(str(logo_siga_static)).replace('\\', '/')
            else:
                # Tenta também em staticfiles/images
                logo_siga_alt = settings.BASE_DIR / 'staticfiles' / 'images' / 'logo-siga-gab.png'
                if logo_siga_alt.exists():
                    logo_siga_path = os.path.abspath(str(logo_siga_alt)).replace('\\', '/')
                else:
                    # Fallback para URL se não encontrar no sistema de arquivos
                    logo_siga_path = request.build_absolute_uri('/static/images/logo-siga-gab.png')

            context = {
                'atendimento': atendimento,
                'nome_instituicao': nome_instituicao,
                'brasao_path': brasao_path,
                'logo_conta_path': logo_conta_path,
                'logo_siga_path': logo_siga_path,
            }
            html_string = render_to_string('atendimentos/relatorio_atendimento_detalhe.html', context)
            # Usa BASE_DIR como base_url para permitir acesso a arquivos locais
            pdf_file = HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf()

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
        queryset = SolicitacaoAgenda.objects.select_related(
            'solicitante', 'conta'
        ).prefetch_related(
            'tramitacoes__usuario'  # Busca o histórico e o usuário de cada entrada
        ).order_by('data_criacao')

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
        status_list = request.query_params.getlist('status')

        if data_inicio and data_fim:
            queryset = queryset.filter(data_criacao__date__range=[data_inicio, data_fim])
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)
        if status_list:
            queryset = queryset.filter(status__in=status_list)

        conta_id = request.query_params.get('conta_id', None)
        conta_contexto = None
        
        if conta_id:
            conta_contexto = Conta.objects.filter(id=conta_id).first()
        elif not request.user.is_superuser and hasattr(request.user, 'perfil'):
            conta_contexto = request.user.perfil.contas.first()
        
        # Prepara as informações de personalização
        nome_instituicao = "Prefeitura Municipal" # Valor padrão
        brasao_path = None
        logo_conta_path = None
        logo_siga_path = None

        if conta_contexto:
            nome_instituicao = conta_contexto.nome_instituicao or nome_instituicao
            if conta_contexto.brasao_instituicao:
                brasao_path = os.path.abspath(conta_contexto.brasao_instituicao.path).replace('\\', '/')
            if conta_contexto.logo_conta:
                logo_conta_path = os.path.abspath(conta_contexto.logo_conta.path).replace('\\', '/')
        
        # Tenta encontrar o logo do SIGA no sistema de arquivos
        logo_siga_static = settings.STATIC_ROOT / 'images' / 'logo-siga-gab.png'
        if logo_siga_static.exists():
            logo_siga_path = os.path.abspath(str(logo_siga_static)).replace('\\', '/')
        else:
            logo_siga_alt = settings.BASE_DIR / 'staticfiles' / 'images' / 'logo-siga-gab.png'
            if logo_siga_alt.exists():
                logo_siga_path = os.path.abspath(str(logo_siga_alt)).replace('\\', '/')
            else:
                logo_siga_path = request.build_absolute_uri('/static/images/logo-siga-gab.png')

        context = {
            'hoje': datetime.now(),
            'solicitacoes': queryset,
            'data_emissao': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
            'usuario_emissao': request.user.get_full_name() or request.user.username,
            'nome_instituicao': nome_instituicao,
            'brasao_path': brasao_path,
            'logo_conta_path': logo_conta_path,
            'logo_siga_path': logo_siga_path,
        }

        try:
            html_string = render_to_string('agendas/relatorio_agendas.html', context)
            pdf_file = HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf()
            
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
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]

    def get(self, request, *args, **kwargs):
        user = request.user
        
        # 1. ORDENAÇÃO
        ordenar_por = request.query_params.get('ordenar_por', 'nome')
        if ordenar_por == 'orgao':
            order_fields = ['orgao', 'nome_completo']
        else:
            order_fields = ['nome_completo']
        
        queryset = Municipe.objects.prefetch_related('perfis__categoria', 'contas').all().order_by(*order_fields)

        from .services.escopo_operador_crm import (
            aplicar_escopo_municipes_queryset,
            categorias_efetivas_request,
        )
        from .services.perfil_municipe import (
            categorias_nomes_de_perfis,
            contas_ids_escopo_usuario,
            linhas_cargo_orgao_de_perfis,
            perfis_para_exibicao,
        )

        categoria_ids = categorias_efetivas_request(request)
        queryset = aplicar_escopo_municipes_queryset(
            queryset,
            user,
            categoria_ids_opcional=categoria_ids if categoria_ids else None,
        )
        contas_escopo = contas_ids_escopo_usuario(user)

        # 4. BUSCA TEXTUAL (q)
        termo_busca = self.request.query_params.get('q', None)
        if termo_busca:
            palavras = termo_busca.split()
            query_palavras_nome = reduce(operator.and_, [
                (Q(nome_completo__icontains=p) | Q(nome_de_guerra__icontains=p)) for p in palavras
            ])
            query_outros_campos = (
                Q(cpf__icontains=termo_busca) |
                Q(emails__contains=[{'email': termo_busca}]) |
                Q(cargo__icontains=termo_busca) |
                Q(orgao__icontains=termo_busca) |
                Q(perfis__categoria__nome__icontains=termo_busca)
            )
            queryset = queryset.filter(query_palavras_nome | query_outros_campos).distinct()

        # 5. GERAÇÃO DO ARQUIVO EXCEL
        from .services.campanhas_email import extract_primary_email, extract_primary_phone

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Contatos'

        headers = ['Nome Completo', 'CPF', 'Data de Nascimento', 'Email Principal', 'Telefone Principal', 'Cargo', 'Órgão', 'Categoria', 'Contas Vinculadas']
        sheet.append(headers)

        for municipe in queryset:
            email_principal = extract_primary_email(municipe) or ''
            telefone = extract_primary_phone(municipe)
            data_nasc_formatada = municipe.data_nascimento.strftime('%d/%m/%Y') if municipe.data_nascimento else ''
            perfis = perfis_para_exibicao(
                municipe,
                categoria_ids=categoria_ids or None,
                contas_ids=contas_escopo,
            )
            categoria_nome = ', '.join(categorias_nomes_de_perfis(perfis))
            linhas_cargo = linhas_cargo_orgao_de_perfis(perfis)
            cargo_exibir = '; '.join(linhas_cargo) if linhas_cargo else municipe.cargo
            orgao_exibir = municipe.orgao if not linhas_cargo else ''
            contas_vinculadas = ", ".join([conta.nome for conta in municipe.contas.all()])

            sheet.append([
                municipe.nome_completo,
                municipe.cpf,
                data_nasc_formatada,
                email_principal,
                telefone,
                cargo_exibir,
                orgao_exibir,
                categoria_nome,
                contas_vinculadas
            ])

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="contatos_{datetime.now().strftime("%Y%m%d")}.xlsx"'
        workbook.save(response)

        return response
    
class GerarPdfMunicipesReportView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]

    def get(self, request, *args, **kwargs):
        user = request.user
        
        ordenar_por = request.query_params.get('ordenar_por', 'nome')
        
        if ordenar_por == 'orgao':
            order_fields = ['orgao', 'nome_completo']
        else:
            order_fields = ['nome_completo']
        
        queryset = Municipe.objects.prefetch_related('perfis__categoria', 'contas').all().order_by(*order_fields)

        from .services.escopo_operador_crm import (
            aplicar_escopo_municipes_queryset,
            categorias_efetivas_request,
        )
        from .services.perfil_municipe import (
            categorias_nomes_de_perfis,
            contas_ids_escopo_usuario,
            linhas_cargo_orgao_de_perfis,
            perfis_para_exibicao,
        )

        categoria_ids = categorias_efetivas_request(request)
        contas_escopo = contas_ids_escopo_usuario(user)
        queryset = aplicar_escopo_municipes_queryset(
            queryset,
            user,
            categoria_ids_opcional=categoria_ids if categoria_ids else None,
        )

        termo_busca = request.query_params.get('q', None)
        if termo_busca:
            palavras = termo_busca.split()
            query_palavras_nome = reduce(operator.and_, [
                (Q(nome_completo__icontains=p) | Q(nome_de_guerra__icontains=p)) for p in palavras
            ])
            query_outros_campos = (
                Q(cpf__icontains=termo_busca) |
                Q(emails__contains=[{'email': termo_busca}]) |
                Q(cargo__icontains=termo_busca) |
                Q(orgao__icontains=termo_busca) |
                Q(perfis__categoria__nome__icontains=termo_busca)
            )
            queryset = queryset.filter(query_palavras_nome | query_outros_campos).distinct()

        
        # PREPARAÇÃO DOS DADOS PARA O TEMPLATE
        from .services.campanhas_email import extract_primary_email, extract_primary_phone

        municipes_data = []
        for municipe in queryset:
            telefone_principal = extract_primary_phone(municipe)
            email_principal = extract_primary_email(municipe) or ''
            perfis = perfis_para_exibicao(
                municipe,
                categoria_ids=categoria_ids or None,
                contas_ids=contas_escopo,
            )
            linhas_cargo = linhas_cargo_orgao_de_perfis(perfis)
            municipes_data.append({
                'nome': municipe.nome_completo,
                'telefone': telefone_principal,
                'email': email_principal,
                'cargo': '; '.join(linhas_cargo) if linhas_cargo else municipe.cargo,
                'orgao': municipe.orgao if not linhas_cargo else '',
                'categoria': ', '.join(categorias_nomes_de_perfis(perfis)),
            })
            
        # CABEÇALHO DO PDF
        conta_id = request.query_params.get('conta_id', None)
        conta_contexto = None
        
        if conta_id:
            conta_contexto = Conta.objects.filter(id=conta_id).first()
        elif not request.user.is_superuser and hasattr(request.user, 'perfil'):
            conta_contexto = request.user.perfil.contas.first()
        
        nome_instituicao = "Prefeitura Municipal"
        brasao_path = None
        logo_conta_path = None
        logo_siga_path = None

        if conta_contexto:
            nome_instituicao = conta_contexto.nome_instituicao or nome_instituicao
            if conta_contexto.brasao_instituicao:
                brasao_path = os.path.abspath(conta_contexto.brasao_instituicao.path).replace('\\', '/')
            if conta_contexto.logo_conta:
                logo_conta_path = os.path.abspath(conta_contexto.logo_conta.path).replace('\\', '/')
        
        # Tenta encontrar o logo do SIGA no sistema de arquivos
        logo_siga_static = settings.STATIC_ROOT / 'images' / 'logo-siga-gab.png'
        if logo_siga_static.exists():
            logo_siga_path = os.path.abspath(str(logo_siga_static)).replace('\\', '/')
        else:
            logo_siga_alt = settings.BASE_DIR / 'staticfiles' / 'images' / 'logo-siga-gab.png'
            if logo_siga_alt.exists():
                logo_siga_path = os.path.abspath(str(logo_siga_alt)).replace('\\', '/')
            else:
                logo_siga_path = request.build_absolute_uri('/static/images/logo-siga-gab.png')

        context = {
            'municipes': municipes_data,
            'data_emissao': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
            'usuario_emissao': request.user.get_full_name() or request.user.username,
            'brasao_path': brasao_path,
            'logo_conta_path': logo_conta_path,
            'logo_siga_path': logo_siga_path,
            # Passa os filtros aplicados para exibir no título do relatório se quiser
            'filtros_texto': termo_busca, 
        }

        try:
            html_string = render_to_string('relatorios/relatorio_municipes.html', context)
            pdf_file = HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf()
            
            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="relatorio_contatos_{datetime.now().strftime("%Y%m%d")}.pdf"'
            return response
        except Exception as e:
            print(f"ERRO INESPERADO AO GERAR PDF DE MUNICIPES: {e}")
            return Response(
                {'detail': f'Ocorreu um erro interno ao gerar o PDF: {e}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class GerarPdfGoogleAgendaView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        # Envolva TODO o código da função em um bloco try...except
        try:
            # --------------------------------------------------------------------
            # A PARTIR DAQUI, TODO O SEU CÓDIGO ORIGINAL DA FUNÇÃO get VEM AQUI,
            # COM UMA INDENTAÇÃO ADICIONAL.
            # --------------------------------------------------------------------
            
            user_logado = request.user
            conta_google_id = request.query_params.get('conta_google_id') or request.query_params.get('agenda_id')
            if not conta_google_id:
                return Response(
                    {'detail': 'conta_google_id é obrigatório para gerar o relatório.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            try:
                conta_google_id = int(conta_google_id)
            except (TypeError, ValueError):
                return Response(
                    {'detail': 'conta_google_id inválido.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if not GoogleCalendarCompatibilityService.usuario_pode_visualizar_eventos(user_logado, conta_google_id):
                return Response(
                    {'detail': 'Você não tem permissão para visualizar esta agenda.'},
                    status=status.HTTP_403_FORBIDDEN
                )

            try:
                conta_google = ContaGoogleCalendar.objects.get(id=conta_google_id, ativa=True)
            except ContaGoogleCalendar.DoesNotExist:
                return Response(
                    {'detail': 'Conta Google não encontrada ou inativa.'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # --- ETAPA 1: RESOLVER CREDENCIAIS DA CONTA SELECIONADA ---
            service = GoogleCalendarCompatibilityService.get_calendar_service(
                user_logado,
                conta_google_id,
                requer_token_proprio=False,
            )
            
            start_date_str = request.query_params.get('data_inicio')
            end_date_str = request.query_params.get('data_fim')
            
            # (Adicionei verificação de segurança para datas nulas, caso o frontend falhe)
            if not start_date_str: start_date_str = timezone.now().strftime('%Y-%m-%d')
            if not end_date_str: end_date_str = start_date_str

            start_date = parse_datetime(start_date_str)
            # Garante que o end_date pegue o dia todo (23:59:59)
            end_date = parse_datetime(end_date_str).replace(hour=23, minute=59, second=59)
            
            # --- CORREÇÃO: AJUSTAR INÍCIO PARA SEMPRE COMEÇAR NA SEGUNDA-FEIRA ---
            # Isso garante que o calendário sempre comece na coluna correta
            # IMPORTANTE: Mantemos start_date_original para buscar eventos do Google
            start_date_original = start_date.date()
            # weekday() retorna: 0=Segunda, 1=Terça, 2=Quarta, 3=Quinta, 4=Sexta, 5=Sábado, 6=Domingo
            dias_para_voltar = start_date_original.weekday()
            # Ajusta o início para a segunda-feira da semana que contém a data inicial (apenas para visualização)
            start_date_ajustado = start_date_original - timedelta(days=dias_para_voltar)
            
            # Busca eventos usando a data ORIGINAL (não a ajustada)
            events_result = service.events().list(
                calendarId=conta_google.calendar_id,
                timeMin=start_date.isoformat() + "Z", # Usa data original para buscar eventos
                timeMax=end_date.isoformat() + "Z",   # Adicione "Z" para UTC
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            # --- ETAPA 3: RENDERIZAR O PDF ---
            eventos_por_dia = defaultdict(list)
            
            # 1. DEFINE QUEM É QUEM
            # Verifica se o usuário é Gestor (Superuser ou Secretária): podem ver eventos "Particular"
            is_gestor = request.user.is_superuser or is_in_group(request.user, 'Secretária')

            for event in events:
                start = event.get('start', {})
                
                # --- REGRA 1: LIMPEZA GERAL (Para Secretária E Consulta) ---
                # Se não tiver 'dateTime', é evento de dia todo (aniversário/lembrete).
                # A gestão pediu para não sair no PDF.
                if 'dateTime' not in start:
                    continue

                summary = event.get('summary', '').strip()

                # --- REGRA 2: PRIVACIDADE (Apenas para perfil Consulta) ---
                # Se o evento começa com "Particular"...
                if summary.lower().startswith('particular'):
                    # ...e o usuário NÃO é gestor, ele não pode ver. Pula.
                    if not is_gestor:
                        continue
                    # Se for gestor, o código segue e adiciona o evento normalmente.

                # --- Tratamento de Local (Mantido) ---
                if 'location' in event:
                    location_completa = event.get('location', '')
                    # Pega só o primeiro nome do local para não poluir o PDF
                    nome_local = location_completa.split(',')[0].strip()
                    event['location'] = nome_local
                    
                # --- Tratamento de Datas (Mantido) ---
                start_str = start.get('dateTime')
                if start_str: # Verificação extra de segurança
                    start_obj = parse_datetime(start_str)
                    dia = start_obj.date()
                    
                    # Injeta o objeto datetime python para o template usar filtros de data
                    event['start']['dateTime'] = start_obj
                    
                    # Adiciona à lista final
                    eventos_por_dia[dia].append(event)
            
            # ... (MANTENHA O RESTANTE DO CÓDIGO DE RENDERIZAÇÃO DO TEMPLATE AQUI IGUAL AO SEU) ...
            # Como você pediu para manter a estrutura, estou assumindo que a lógica de 
            # while data_corrente <= data_final_loop, etc, continua aqui.
            
            # --- CONSTRUÇÃO MANUAL DE SEMANAS COMECANDO NA SEGUNDA-FEIRA CORRETA ---
            meses_do_relatorio = []
            
            # Calcula segunda-feira da semana que contém a data inicial
            start_date_original = start_date.date()
            dias_para_voltar = start_date_original.weekday()  # 0=Seg, 1=Ter, ..., 6=Dom
            segunda_feira_inicio = start_date_original - timedelta(days=dias_para_voltar)
            
            # Calcula domingo da semana que contém a data final
            end_date_final = end_date.date()
            dias_para_avancar = 6 - end_date_final.weekday()  # Dias até domingo
            domingo_fim = end_date_final + timedelta(days=dias_para_avancar)
            
            # Constrói semanas manualmente começando da segunda-feira correta
            data_atual = segunda_feira_inicio
            semanas_agrupadas_por_mes = defaultdict(list)
            
            while data_atual <= domingo_fim:
                # Determina qual mês/ano estamos
                mes_ano = (data_atual.year, data_atual.month)
                
                # Constrói uma semana (segunda a domingo)
                semana = []
                for i in range(7):  # 7 dias da semana
                    dia_semana = data_atual + timedelta(days=i)
                    # Sempre adiciona o dia, mas só mostra eventos se estiver no período selecionado
                    if start_date_original <= dia_semana <= end_date_final:
                        # Dia dentro do período: mostra eventos
                        semana.append({
                            'data': dia_semana,
                            'eventos': eventos_por_dia.get(dia_semana, [])
                        })
                    else:
                        # Dia fora do período: aparece vazio (mas mantém estrutura da semana)
                        semana.append({
                            'data': dia_semana,
                            'eventos': []
                        })
                
                # Adiciona semana ao mês correspondente
                semanas_agrupadas_por_mes[mes_ano].append(semana)
                
                # Avança para a próxima semana (segunda-feira)
                data_atual += timedelta(days=7)
            
            # Organiza por mês para o template
            nomes_dos_meses = [
                'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
                'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'
            ]
            
            for (ano, mes), semanas in sorted(semanas_agrupadas_por_mes.items()):
                nome_mes_pt = nomes_dos_meses[mes - 1]
                meses_do_relatorio.append({
                    'nome_mes': f"{nome_mes_pt} de {ano}",
                    'mes_numero': mes,
                    'semanas': semanas
                })

            logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-brasao-prefeitura.png')
            logo_data = ""
            if os.path.exists(logo_path):
                with open(logo_path, "rb") as image_file:
                    logo_data = base64.b64encode(image_file.read()).decode('utf-8')

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
            tb_string = traceback.format_exc()
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
        
        # Aplicar filtro de conta no queryset
        if conta_id:
            queryset = queryset.filter(conta_destino_id=conta_id)
            conta_contexto = Conta.objects.filter(id=conta_id).first()
        elif not request.user.is_superuser and hasattr(request.user, 'perfil'):
            conta_contexto = request.user.perfil.contas.first()
            if conta_contexto:
                queryset = queryset.filter(conta_destino__in=request.user.perfil.contas.all())
        
        # Prepara as informações de personalização
        nome_instituicao = "Prefeitura Municipal" # Valor padrão
        brasao_path = None
        logo_conta_path = None
        logo_siga_path = None

        if conta_contexto:
            nome_instituicao = conta_contexto.nome_instituicao or nome_instituicao
            if conta_contexto.brasao_instituicao:
                brasao_path = os.path.abspath(conta_contexto.brasao_instituicao.path).replace('\\', '/')
            if conta_contexto.logo_conta:
                logo_conta_path = os.path.abspath(conta_contexto.logo_conta.path).replace('\\', '/')
        
        # Tenta encontrar o logo do SIGA no sistema de arquivos
        logo_siga_static = settings.STATIC_ROOT / 'images' / 'logo-siga-gab.png'
        if logo_siga_static.exists():
            logo_siga_path = os.path.abspath(str(logo_siga_static)).replace('\\', '/')
        else:
            logo_siga_alt = settings.BASE_DIR / 'staticfiles' / 'images' / 'logo-siga-gab.png'
            if logo_siga_alt.exists():
                logo_siga_path = os.path.abspath(str(logo_siga_alt)).replace('\\', '/')
            else:
                logo_siga_path = request.build_absolute_uri('/static/images/logo-siga-gab.png')

        # Calcular quantitativos
        # 1. Munícipe mais presente (só se tiver registro)
        municipe_mais_presente = None
        if queryset.exists():
            municipe_counts = queryset.values('municipe').annotate(
                total=Count('id')
            ).order_by('-total').first()
            if municipe_counts:
                try:
                    municipe_mais_presente_obj = Municipe.objects.get(id=municipe_counts['municipe'])
                    municipe_mais_presente = {
                        'nome': municipe_mais_presente_obj.nome_completo,
                        'total': municipe_counts['total']
                    }
                except Municipe.DoesNotExist:
                    pass
        
        # 2. Quantidade de checkins por conta
        checkins_por_conta = queryset.values('conta_destino__nome').annotate(
            total=Count('id')
        ).order_by('-total')
        
        # 3. Dias com mais volumes (top 5 dias)
        # Usar timezone.localtime para converter para timezone local antes de truncar
        from django.db.models.functions import Cast
        from django.db.models import DateField
        
        dias_com_mais_volumes = []
        if queryset.exists():
            # Agrupar manualmente por data usando timezone local
            visitas_por_dia = {}
            for visita in queryset:
                data_local = timezone.localtime(visita.data_checkin).date()
                if data_local not in visitas_por_dia:
                    visitas_por_dia[data_local] = 0
                visitas_por_dia[data_local] += 1
            
            # Converter para lista e ordenar
            dias_com_mais_volumes = [
                {'dia': dia, 'total': total}
                for dia, total in sorted(visitas_por_dia.items(), key=lambda x: x[1], reverse=True)[:5]
            ]
        
        # Prepara o contexto para o template
        context = {
            'visitas': queryset,
            'nome_instituicao': nome_instituicao,
            'brasao_path': brasao_path,
            'logo_conta_path': logo_conta_path,
            'logo_siga_path': logo_siga_path,
            'municipe_mais_presente': municipe_mais_presente,
            'checkins_por_conta': checkins_por_conta,
            'dias_com_mais_volumes': dias_com_mais_volumes,
        }

        # Renderiza o HTML e converte para PDF
        html_string = render_to_string('relatorios/relatorio_checkins.html', context)
        pdf_file = HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf()

        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="relatorio_checkins_{datetime.now().strftime("%Y%m%d")}.pdf"'
        return response
    
class GerarDossieMunicipePdfView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]

    def get(self, request, pk, *args, **kwargs):
        try:
            user = request.user
            municipe = Municipe.objects.get(pk=pk)

            # --- LEITURA DOS PARÂMETROS ---
            escopo = request.query_params.get('escopo', 'total')
            secoes_param = request.query_params.get('secoes', 'atendimentos,agendas,eventos')
            secoes_lista = secoes_param.split(',')

            # QuerySets Base
            qs_atendimentos = municipe.atendimentos.all()
            qs_agendas = municipe.solicitacoes_agenda.all()
            from eventos.models import Convidado
            qs_convites = Convidado.objects.filter(perfil__municipe=municipe).select_related('evento').all()

            # --- FILTRO 1: SEGURANÇA GERAL (Isolamento de Contas) ---
            if not user.is_superuser:
                if hasattr(user, 'perfil'):
                    contas_permitidas = user.perfil.contas.all()
                    qs_atendimentos = qs_atendimentos.filter(conta__in=contas_permitidas)
                    qs_agendas = qs_agendas.filter(conta__in=contas_permitidas)
                    qs_convites = qs_convites.filter(evento__conta__in=contas_permitidas)
                else:
                    return Response({'detail': 'Sem permissão.'}, status=403)

            # --- FILTRO 2: ESCOPO ("Meus Atendimentos") ---
            if escopo == 'meus':
                # Filtra atendimentos onde o usuário é o RESPONSÁVEL
                qs_atendimentos = qs_atendimentos.filter(responsavel=user)
                # Para agendas e eventos, mantemos a visão da conta, pois 'meus' 
                # geralmente se refere à responsabilidade técnica do atendimento.
                # Se quiser filtrar agendas criadas por ele, use: .filter(solicitante=...) ou logica custom.

            # --- FILTRO 3: SELEÇÃO DE SEÇÕES (Checkbox) ---
            # Se não estiver na lista, passamos None para o template não renderizar nada
            
            exibir_atendimentos = 'atendimentos' in secoes_lista
            if not exibir_atendimentos:
                qs_atendimentos = None

            exibir_agendas = 'agendas' in secoes_lista
            if not exibir_agendas:
                qs_agendas = None

            exibir_eventos = 'eventos' in secoes_lista
            if not exibir_eventos:
                qs_convites = [] # Lista vazia para o loop abaixo não quebrar
                historico_eventos = None
            else:
                # Processa eventos se selecionado
                historico_eventos = []
                for convite in qs_convites.order_by('-evento__data_evento'):
                    historico_eventos.append({
                        'data': convite.evento.data_evento,
                        'nome': convite.evento.nome,
                        'status': convite.get_status_display(),
                        'local': convite.evento.local
                    })

            # --- GERAÇÃO DO CONTEXTO ---
            # (Mantido o código de conta_contexto e brasão igual ao anterior...)
            conta_contexto = None
            if not user.is_superuser and hasattr(user, 'perfil'):
                conta_contexto = user.perfil.contas.first()
            
            nome_instituicao = "Prefeitura Municipal"
            brasao_url = ''
            logo_conta_url = ''
            
            if conta_contexto:
                 # ... (Logica de brasao igual ao anterior) ...
                 pass 

            context = {
                'municipe': municipe,
                
                # Dados passados ou None
                'atendimentos': qs_atendimentos.order_by('-data_criacao') if qs_atendimentos else None,
                'agendas': qs_agendas.order_by('-data_criacao') if qs_agendas else None,
                'eventos': historico_eventos,

                # Flags explícitas para o template saber se deve desenhar a seção
                'exibir_atendimentos': exibir_atendimentos,
                'exibir_agendas': exibir_agendas,
                'exibir_eventos': exibir_eventos,

                'data_emissao': datetime.now(),
                'usuario_emissao': user.get_full_name() or user.username,
                # ... outros dados de layout
            }

            html_string = render_to_string('relatorios/dossie_municipe.html', context)
            pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()
            
            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="Dossie.pdf"'
            return response

        except Exception as e:
            # traceback.print_exc()
            return Response({'detail': str(e)}, status=500)

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
        base_queryset = Municipe.objects.filter(ativo=True)
        from .services.escopo_operador_crm import aplicar_escopo_municipes_queryset

        base_queryset = aplicar_escopo_municipes_queryset(base_queryset, user)

        data_param = self.request.query_params.get('data', None) 

        if data_param:
            try:
                data_selecionada = datetime.strptime(data_param, '%Y-%m-%d').date()
            except ValueError:
                return Municipe.objects.none()
        else:
            data_selecionada = timezone.localdate() 

        return base_queryset.filter(
            data_nascimento__day=data_selecionada.day,
            data_nascimento__month=data_selecionada.month
        )


class GerarPdfAniversariantesDoDiaView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        data_param = request.query_params.get("data")
        data_selecionada = parse_date(data_param) if data_param else timezone.localdate()
        if not data_selecionada:
            return Response(
                {"detail": "Data inválida. Use o formato YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.is_superuser:
            base_queryset = Municipe.objects.filter(ativo=True)
            contas_contexto = None
        elif hasattr(user, "perfil"):
            contas_contexto = list(user.perfil.contas.all())
            from .services.escopo_operador_crm import aplicar_escopo_municipes_queryset

            base_queryset = aplicar_escopo_municipes_queryset(
                Municipe.objects.filter(ativo=True), user
            )
        else:
            base_queryset = Municipe.objects.none()
            contas_contexto = None

        aniversariantes = base_queryset.filter(
            data_nascimento__day=data_selecionada.day,
            data_nascimento__month=data_selecionada.month,
        ).order_by("nome_completo")

        linhas = []
        for municipe in aniversariantes:
            telefone_principal = "-"
            telefones = getattr(municipe, "telefones", None) or []
            if isinstance(telefones, list) and telefones:
                primeiro_tel = telefones[0]
                if isinstance(primeiro_tel, dict):
                    telefone_principal = primeiro_tel.get("numero") or "-"
                else:
                    telefone_principal = str(primeiro_tel) or "-"

            perfis_qs = PerfilMunicipe.objects.filter(municipe=municipe, ativo=True)
            if contas_contexto is not None:
                perfis_qs = perfis_qs.filter(conta__in=contas_contexto)
            perfil_ref = perfis_qs.select_related("categoria").order_by("-id").first()
            cargo_ref = "-"
            categoria_ref = "-"
            if perfil_ref:
                cargo_ref = (perfil_ref.cargo or perfil_ref.instituicao or perfil_ref.departamento or "-").strip() or "-"
                categoria_ref = perfil_ref.categoria.nome if perfil_ref.categoria else "-"

            linhas.append(
                {
                    "nome": municipe.nome_completo,
                    "cargo": cargo_ref,
                    "telefone": telefone_principal,
                    "categoria": categoria_ref,
                }
            )

        context = {
            "data_aniversario": data_selecionada,
            "total_aniversariantes": len(linhas),
            "aniversariantes": linhas,
            "gerado_em": timezone.localtime(),
            "usuario_emissao": user.get_full_name() or user.username,
        }

        try:
            html = render_to_string("relatorios/aniversariantes_do_dia_pdf.html", context)
            pdf_file = HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
            response = HttpResponse(pdf_file, content_type="application/pdf")
            response["Content-Disposition"] = (
                f'attachment; filename="aniversariantes_{data_selecionada.strftime("%Y%m%d")}.pdf"'
            )
            return response
        except Exception as exc:
            return Response(
                {"detail": f"Erro ao gerar relatório de aniversariantes: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BuscaGlobalView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _usar_ia(request) -> bool:
        valor = (request.query_params.get('ia') or '').strip().lower()
        return valor in ('1', 'true', 'yes')

    @staticmethod
    def _municipe_queryset_global(user):
        if user.is_superuser:
            return Municipe.objects.all()
        if hasattr(user, 'perfil') and is_in_group(
            user, ['Recepção', 'Membro do Gabinete', 'Secretária']
        ):
            return Municipe.objects.filter(contas__in=user.perfil.contas.all()).distinct()
        return Municipe.objects.none()

    @staticmethod
    def _atendimento_queryset_global(user):
        if is_in_group(user, 'Recepção'):
            return Atendimento.objects.none()
        queryset = Atendimento.objects.all()
        if user.is_superuser:
            return queryset
        if hasattr(user, 'perfil'):
            return queryset.filter(conta__in=user.perfil.contas.all()).filter(
                Q(responsavel=user) | Q(responsavel__isnull=True)
            )
        return Atendimento.objects.none()

    @classmethod
    def _usuario_pode_ver_atendimento_global(cls, atendimento, user):
        return cls._atendimento_queryset_global(user).filter(id=atendimento.id).exists()

    def _busca_textual_global(self, user, termo_busca):
        from .services.busca_textual import filtrar_queryset_atendimento, filtrar_queryset_municipe

        resultados = []
        atendimento_qs = self._atendimento_queryset_global(user)
        atendimentos_encontrados = (
            filtrar_queryset_atendimento(atendimento_qs, termo_busca)
            .order_by('-data_criacao')[:5]
        )
        for atendimento in atendimentos_encontrados:
            resultados.append({
                'tipo': 'atendimento',
                'id': atendimento.id,
                'texto_principal': f"Protocolo {atendimento.protocolo}",
                'texto_secundario': atendimento.titulo,
                'url': f"/atendimentos/{atendimento.id}",
                'modo_busca': 'textual',
            })

        municipe_qs = self._municipe_queryset_global(user)
        municipes_encontrados = (
            filtrar_queryset_municipe(municipe_qs, termo_busca)
            .order_by('nome_completo')[:100]
        )
        for municipe in municipes_encontrados:
            resultados.append({
                'tipo': 'municipe',
                'id': municipe.id,
                'texto_principal': municipe.nome_completo,
                'texto_secundario': f"CPF: {municipe.cpf or 'Não informado'}",
                'url': f"/municipes/{municipe.id}/historico",
                'modo_busca': 'textual',
            })
        return resultados

    def _busca_ia_global(self, user, termo_busca):
        from .services.ia_intelligence import (
            buscar_atendimentos_semantico_otimizado,
            buscar_municipes_semantico,
        )

        resultados = []
        municipe_qs = self._municipe_queryset_global(user)
        municipe_ids_permitidos = set(municipe_qs.values_list('id', flat=True))

        if not is_in_group(user, 'Recepção'):
            raw_atendimentos = []
            if user.is_superuser:
                raw_atendimentos = buscar_atendimentos_semantico_otimizado(
                    termo_busca, conta_id=None, top_k=10
                )
            elif hasattr(user, 'perfil'):
                for conta in user.perfil.contas.all():
                    raw_atendimentos.extend(
                        buscar_atendimentos_semantico_otimizado(
                            termo_busca, conta_id=conta.id, top_k=5
                        )
                    )

            por_id = {}
            for item in raw_atendimentos:
                atendimento = item['atendimento']
                if not self._usuario_pode_ver_atendimento_global(atendimento, user):
                    continue
                atual = por_id.get(atendimento.id)
                if atual is None or item['score'] > atual['score']:
                    por_id[atendimento.id] = item

            for item in sorted(por_id.values(), key=lambda x: x['score'], reverse=True)[:5]:
                atendimento = item['atendimento']
                resultados.append({
                    'tipo': 'atendimento',
                    'id': atendimento.id,
                    'texto_principal': f"Protocolo {atendimento.protocolo}",
                    'texto_secundario': item.get('snippet') or atendimento.titulo,
                    'url': f"/atendimentos/{atendimento.id}",
                    'score_match': item['score_percentual'],
                    'modo_busca': 'ia',
                })

        raw_municipes = buscar_municipes_semantico(termo_busca, limite=20)
        municipes_adicionados = 0
        for item in raw_municipes:
            municipe = item['municipe']
            if municipe.id not in municipe_ids_permitidos:
                continue
            cargo = _formatar_cargo_orgao_municipe(municipe)
            resultados.append({
                'tipo': 'municipe',
                'id': municipe.id,
                'texto_principal': municipe.nome_completo,
                'texto_secundario': cargo or f"CPF: {municipe.cpf or 'Não informado'}",
                'url': f"/municipes/{municipe.id}/historico",
                'score_match': item['score_percentual'],
                'modo_busca': 'ia',
            })
            municipes_adicionados += 1
            if municipes_adicionados >= 20:
                break

        return resultados

    def get(self, request, *args, **kwargs):
        termo_busca = self.request.query_params.get('q', None)
        if not termo_busca or len(termo_busca) < 3:
            return Response([])

        user = self.request.user
        usar_ia = self._usar_ia(request)
        ia_fallback = False
        resultados = []

        if usar_ia:
            try:
                resultados = self._busca_ia_global(user, termo_busca)
            except Exception as exc:
                logger.warning("Busca global IA indisponível, fallback textual: %s", exc)
                resultados = []
                ia_fallback = True

            if not resultados:
                resultados = self._busca_textual_global(user, termo_busca)
                ia_fallback = True
        else:
            resultados = self._busca_textual_global(user, termo_busca)

        serializer = BuscaGlobalSerializer(resultados, many=True)
        if usar_ia:
            return Response({
                'modo_busca': 'ia' if not ia_fallback else 'textual',
                'ia_fallback': ia_fallback,
                'resultados': serializer.data,
            })
        return Response(serializer.data)


# -----------------------------------------------------------------------------
# Views de Lembretes
# -----------------------------------------------------------------------------

class LembreteListCreateView(generics.ListCreateAPIView):
    serializer_class = LembreteSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageLembretes]
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        
        print(f"--- INICIANDO BUSCA DE LEMBRETES PARA O USUÁRIO: {user.username} ---")

        if user.is_superuser:
            queryset = Lembrete.objects.all()
        elif hasattr(user, 'perfil'):
            user_contas = user.perfil.contas.all()
            queryset = Lembrete.objects.filter(conta__in=user_contas)
        else:
            return Lembrete.objects.none()

        data_inicio_str = self.request.query_params.get('data_inicio', None)
        data_fim_str = self.request.query_params.get('data_fim', None)
        
        print(f"[DIAGNÓSTICO] Filtros de data recebidos: Início='{data_inicio_str}', Fim='{data_fim_str}'")

        if data_inicio_str and data_fim_str:
            try:
                # Converte as strings de data em objetos de data do Python
                inicio_date = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
                fim_date = datetime.strptime(data_fim_str, '%Y-%m-%d').date()

                # Cria datetimes completos, do primeiro segundo do dia de início
                # até o último segundo do dia de fim.
                inicio_datetime = timezone.make_aware(datetime.combine(inicio_date, time.min))
                fim_datetime = timezone.make_aware(datetime.combine(fim_date, time.max))
                
                # Usa o filtro __range, que é mais confiável para datetime
                queryset = queryset.filter(data_criacao__range=(inicio_datetime, fim_datetime))
            except (ValueError, TypeError):
                # Se as datas forem inválidas, não faz nada e retorna a lista completa
                pass
        
        print(f"[DIAGNÓSTICO] Total de lembretes após o filtro de data: {queryset.count()}")
        print("--- FIM DA BUSCA DE LEMBRETES ---")

        return queryset.order_by('-data_criacao')

    def perform_create(self, serializer):
        serializer.save(usuario=self.request.user)

class GerarPdfLembretesView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanManageLembretes]

    def get(self, request, *args, **kwargs):
        user = request.user
        
        # Inicia a queryset com a mesma lógica de permissão da listagem
        if user.is_superuser:
            queryset = Lembrete.objects.all()
        elif hasattr(user, 'perfil'):
            queryset = Lembrete.objects.filter(conta__in=user.perfil.contas.all())
        else:
            queryset = Lembrete.objects.none()

        # Reaproveita a mesma lógica de filtro de data que já validamos
        data_inicio_str = self.request.query_params.get('data_inicio', None)
        data_fim_str = self.request.query_params.get('data_fim', None)

        if data_inicio_str and data_fim_str:
            try:
                inicio_date = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
                fim_date = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
                inicio_datetime = timezone.make_aware(datetime.combine(inicio_date, time.min))
                fim_datetime = timezone.make_aware(datetime.combine(fim_date, time.max))
                queryset = queryset.filter(data_criacao__range=(inicio_datetime, fim_datetime))
            except (ValueError, TypeError):
                queryset = Lembrete.objects.none()

        # Preparação do contexto para o template
        conta_contexto = request.user.perfil.contas.first() if hasattr(request.user, 'perfil') else None
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
            'lembretes': queryset.select_related('conta', 'usuario').order_by('-data_criacao'),
            'data_inicio': datetime.strptime(data_inicio_str, '%Y-%m-%d') if data_inicio_str else None,
            'data_fim': datetime.strptime(data_fim_str, '%Y-%m-%d') if data_fim_str else None,
            'nome_instituicao': nome_instituicao,
            'brasao_url': brasao_url,
            'logo_conta_url': logo_conta_url,
            'data_emissao': timezone.now(),
            'usuario_emissao': request.user.get_full_name() or request.user.username,
            'logo_siga_url': request.build_absolute_uri('/static/images/logo-siga-gab.png'),
        }

        html_string = render_to_string('relatorios/relatorio_lembretes.html', context)
        pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()

        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="relatorio_lembretes_{timezone.now().strftime("%Y%m%d")}.pdf"'
        return response

class LembreteDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = LembreteSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageLembretes]

    def get_queryset(self):
        user = self.request.user
        
        if user.is_superuser:
            return Lembrete.objects.all()

        if hasattr(user, 'perfil'):
            return Lembrete.objects.filter(conta__in=user.perfil.contas.all())
            
        return Lembrete.objects.none()


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
        
        # 1. Busca a solicitação de agenda
        try:
            solicitacao = SolicitacaoAgenda.objects.get(pk=pk)
        except SolicitacaoAgenda.DoesNotExist:
            return Response({'detail': 'Solicitação de agenda não encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        
        # 2. Tenta usar novo sistema de múltiplas contas Google (compatibilidade)
        try:
            # Busca serviço usando compatibilidade (usa conta padrão)
            service = GoogleCalendarCompatibilityService.get_calendar_service_legado(user)
        except (GoogleCalendarAuthError, GoogleCalendarPermissionError) as e:
            return Response({
                'detail': f'Erro de autorização do Google: {str(e)}. Faça login novamente nas configurações.'
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            # Fallback para sistema legado se novo sistema falhar
            try:
                token_google = GoogleApiToken.objects.get(usuario=user)
            except GoogleApiToken.DoesNotExist:
                return Response({
                    'detail': 'Autorização do Google não encontrada. Faça login nas configurações.'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Usa sistema legado
            credentials = Credentials(
                token=token_google.access_token,
                refresh_token=token_google.refresh_token,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=settings.GOOGLE_CLIENT_ID,
                client_secret=settings.GOOGLE_CLIENT_SECRET,
                scopes=['https://www.googleapis.com/auth/calendar.events']
            )
            
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(GoogleAuthRequest())
                token_google.access_token = credentials.token
                token_google.save()
            
            service = build('calendar', 'v3', credentials=credentials)

        # 3. Garante que a solicitação está no status correto para ser agendada
        if solicitacao.status != 'AGENDADO' or not solicitacao.data_agendada or not solicitacao.data_agendada_fim:
            return Response({'detail': 'Esta solicitação não está confirmada ou não possui data/hora definidas.'}, status=status.HTTP_400_BAD_REQUEST)

        # 4. Monta o evento a ser criado
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
            # Busca calendar_id da conta padrão ou usa 'primary' como fallback
            calendar_id = 'primary'
            try:
                # Tenta buscar conta Google padrão do usuário
                conta_usuario = None
                if hasattr(user, 'perfil') and hasattr(user.perfil, 'contas'):
                    conta_usuario = user.perfil.contas.first()
                
                if conta_usuario and conta_usuario.conta_google_padrao:
                    calendar_id = conta_usuario.conta_google_padrao.calendar_id
            except Exception:
                # Se falhar, usa 'primary' (comportamento original)
                pass
            
            evento_criado = service.events().insert(calendarId=calendar_id, body=evento).execute()
            
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
            # Só superadmin e Secretária podem ver eventos que começam com "Particular"
            pode_ver_particular = request.user.is_superuser or is_in_group(request.user, 'Secretária')
            eventos_formatados = []
            for event in events:
                summary = event.get('summary', '')
                if summary.strip().lower().startswith('particular') and not pode_ver_particular:
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
        is_recorrente = self.request.data.get('is_recorrente', False)
        
        if not is_recorrente:
            serializer.save(responsavel=self.request.user)
            return

        frequencia = self.request.data.get('frequencia')
        data_fim_recorrencia_str = self.request.data.get('data_fim_recorrencia')
        start_date_str = self.request.data.get('data_inicio')
        end_date_str = self.request.data.get('data_fim')

        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        recurrence_end_date = datetime.strptime(data_fim_recorrencia_str, '%Y-%m-%d').date()
        
        duracao_evento = end_date - start_date
        grupo_id = uuid.uuid4()
        
        datas_para_criar = []
        data_corrente = start_date
        
        if frequencia == 'SEMANAL':
            while data_corrente.date() <= recurrence_end_date:
                datas_para_criar.append(data_corrente)
                data_corrente += timedelta(weeks=1)

        if not datas_para_criar:
             serializer.save(responsavel=self.request.user)
             return

        reservas_conflitantes = ReservaEspaco.objects.filter(
            espaco_id=self.request.data.get('espaco'),
            data_inicio__lt=datas_para_criar[-1] + duracao_evento,
            data_fim__gt=datas_para_criar[0]
        )

        for data_inicio_recorrencia in datas_para_criar:
            data_fim_recorrencia = data_inicio_recorrencia + duracao_evento
            if reservas_conflitantes.filter(data_inicio__lt=data_fim_recorrencia, data_fim__gt=data_inicio_recorrencia).exists():
                raise serializers.ValidationError(
                    f"Conflito de agendamento detectado para o dia {data_inicio_recorrencia.strftime('%d/%m/%Y')}. "
                    "Uma ou mais datas no período recorrente já estão ocupadas."
                )

        # --- INÍCIO DA CORREÇÃO ---
        # Copiamos os dados validados para um novo dicionário
        dados_base = serializer.validated_data.copy()
        # Removemos as chaves de data originais para evitar o conflito
        dados_base.pop('data_inicio', None)
        dados_base.pop('data_fim', None)
        # --- FIM DA CORREÇÃO ---

        for data_inicio_recorrencia in datas_para_criar:
            data_fim_recorrencia = data_inicio_recorrencia + duracao_evento
            ReservaEspaco.objects.create(
                **dados_base, # Usamos o dicionário limpo
                responsavel=self.request.user,
                data_inicio=data_inicio_recorrencia,
                data_fim=data_fim_recorrencia,
                grupo_recorrencia=grupo_id
            )

class ReservaEspacoDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = ReservaEspaco.objects.all()
    serializer_class = ReservaEspacoSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageReservas]

    def perform_update(self, serializer):
        instance = serializer.save()
        # Se o evento pertence a um grupo recorrente, replicamos a alteração de texto
        if instance.grupo_recorrencia:
            ReservaEspaco.objects.filter(grupo_recorrencia=instance.grupo_recorrencia).update(
                titulo=instance.titulo,
                observacoes=instance.observacoes
            )

    def perform_destroy(self, instance):
        # O frontend agora envia 'unica' ou 'serie'
        escopo = self.request.query_params.get('escopo', 'unica')

        if escopo == 'serie' and instance.grupo_recorrencia:
            # Exclui toda a série
            ReservaEspaco.objects.filter(grupo_recorrencia=instance.grupo_recorrencia).delete()
        else:
            # Exclui apenas a instância única (comportamento padrão)
            instance.delete()


class GerarPdfEspacosPeriodoView(APIView):
    """
    Relatório PDF em grade de calendário (estilo Google Agenda) com reservas e
  solicitações de agenda agendadas por período e, opcionalmente, por espaço.
    """
    permission_classes = [permissions.IsAuthenticated, CanManageReservas]

    def _filtrar_por_contas(self, queryset, user):
        if user.is_superuser:
            return queryset
        if hasattr(user, 'perfil'):
            return queryset.filter(espaco__contas__in=user.perfil.contas.all()).distinct()
        return queryset.none()

    def _evento_reserva(self, reserva):
        inicio = timezone.localtime(reserva.data_inicio)
        fim = timezone.localtime(reserva.data_fim)
        return {
            'summary': reserva.titulo,
            'espaco_nome': reserva.espaco.nome,
            'hora_inicio': inicio.strftime('%H:%M'),
            'hora_fim': fim.strftime('%H:%M'),
            'solicitante': reserva.solicitante.nome_completo if reserva.solicitante else '',
            'tipo': 'reserva',
            '_sort': inicio,
        }

    def _evento_agenda(self, agenda):
        inicio = timezone.localtime(agenda.data_agendada)
        fim = timezone.localtime(agenda.data_agendada_fim)
        return {
            'summary': agenda.assunto,
            'espaco_nome': agenda.espaco.nome if agenda.espaco else '—',
            'hora_inicio': inicio.strftime('%H:%M'),
            'hora_fim': fim.strftime('%H:%M'),
            'solicitante': agenda.solicitante.nome_completo if agenda.solicitante else '',
            'tipo': 'agenda',
            '_sort': inicio,
        }

    def get(self, request, *args, **kwargs):
        data_inicio_str = request.query_params.get('data_inicio')
        data_fim_str = request.query_params.get('data_fim')
        espaco_id = request.query_params.get('espaco_id')

        if not data_inicio_str or not data_fim_str:
            return Response(
                {'detail': 'Informe data_inicio e data_fim (YYYY-MM-DD).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            start_date = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'detail': 'Datas inválidas. Use o formato YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if end_date < start_date:
            return Response(
                {'detail': 'data_fim deve ser igual ou posterior a data_inicio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inicio_dt = timezone.make_aware(datetime.combine(start_date, time.min))
        fim_dt = timezone.make_aware(datetime.combine(end_date, time.max))

        reservas_qs = ReservaEspaco.objects.filter(
            data_inicio__lte=fim_dt,
            data_fim__gte=inicio_dt,
        ).select_related('espaco', 'solicitante')
        reservas_qs = self._filtrar_por_contas(reservas_qs, request.user)

        agendas_qs = SolicitacaoAgenda.objects.filter(
            status='AGENDADO',
            espaco__isnull=False,
            data_agendada__isnull=False,
            data_agendada_fim__isnull=False,
            data_agendada__lte=fim_dt,
            data_agendada_fim__gte=inicio_dt,
        ).select_related('espaco', 'solicitante')
        agendas_qs = self._filtrar_por_contas(agendas_qs, request.user)

        espaco_label = 'Todos os espaços'
        if espaco_id:
            try:
                espaco_id_int = int(espaco_id)
            except (TypeError, ValueError):
                return Response({'detail': 'espaco_id inválido.'}, status=status.HTTP_400_BAD_REQUEST)
            reservas_qs = reservas_qs.filter(espaco_id=espaco_id_int)
            agendas_qs = agendas_qs.filter(espaco_id=espaco_id_int)
            espaco_obj = Espaco.objects.filter(pk=espaco_id_int).first()
            if espaco_obj:
                espaco_label = espaco_obj.nome

        eventos_por_dia = defaultdict(list)

        for reserva in reservas_qs:
            dia = timezone.localtime(reserva.data_inicio).date()
            if start_date <= dia <= end_date:
                eventos_por_dia[dia].append(self._evento_reserva(reserva))

        for agenda in agendas_qs:
            dia = timezone.localtime(agenda.data_agendada).date()
            if start_date <= dia <= end_date:
                eventos_por_dia[dia].append(self._evento_agenda(agenda))

        for dia in eventos_por_dia:
            eventos_por_dia[dia].sort(key=lambda e: e.get('_sort') or datetime.min)
            for ev in eventos_por_dia[dia]:
                ev.pop('_sort', None)

        meses_do_relatorio = build_meses_do_relatorio(start_date, end_date, eventos_por_dia)
        periodo_label = f"{start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')}"

        context = {
            'hoje': timezone.now().date(),
            'meses_do_relatorio': meses_do_relatorio,
            'periodo_label': periodo_label,
            'espaco_label': espaco_label,
            'usuario_emissao': request.user.get_full_name() or request.user.username,
        }

        try:
            html_string = render_to_string('espacos/relatorio_espacos_periodo.html', context)
            pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()
            nome_arquivo = f"relatorio_espacos_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.pdf"
            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{nome_arquivo}"'
            return response
        except Exception as e:
            logger.exception('Erro ao gerar PDF de espaços: %s', e)
            return Response(
                {'detail': f'Ocorreu um erro ao gerar o PDF: {e}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


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

# --- NOVA VIEW PARA GERAR PDF DE OFÍCIO ---
# (Colada do arquivo oficios/views.py)
class GerarPdfOficioView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        try:
            # Otimização da query
            oficio = get_object_or_404(Oficio.objects.select_related('conta', 'criado_por'), pk=pk)

            user = request.user
            if not user.is_superuser:
                if not hasattr(user, 'perfil') or oficio.conta not in user.perfil.contas.all():
                    return Response({"detail": "Você não tem permissão para acessar este ofício."}, status=403)
            
            # --- CORREÇÃO DO BRASÃO (Caminho Físico) ---
            # Tenta pegar da pasta de produção (staticfiles) ou desenvolvimento (static)
            
            # Defina o nome exato do arquivo (confira se é .jpg ou .png no seu servidor)
            nome_arquivo = 'brasao_prefeitura.jpg' 
            
            caminho_imagem = os.path.join(settings.BASE_DIR, 'staticfiles', 'images', nome_arquivo)
            
            # Fallback: Se não achar no staticfiles, tenta no static normal (src)
            if not os.path.exists(caminho_imagem):
                caminho_imagem = os.path.join(settings.BASE_DIR, 'static', 'images', nome_arquivo)

            # Prepara a URL de arquivo local (file://)
            # Isso faz o WeasyPrint ler direto do disco, sem depender de rede
            brasao_uri = f"file://{caminho_imagem}"

            # --- ASSINATURA ELETRÔNICA ---
            assinatura_url = None
            if oficio.conta.usar_assinatura_eletronica and oficio.conta.assinatura_eletronica:
                try:
                    # Verifica se o arquivo existe fisicamente
                    if os.path.exists(oficio.conta.assinatura_eletronica.path):
                        # Usa file:// para o WeasyPrint ler direto do disco
                        assinatura_url = f"file://{oficio.conta.assinatura_eletronica.path}"
                except Exception as e:
                    # Se houver erro ao acessar o arquivo, continua sem assinatura
                    print(f"Erro ao carregar assinatura eletrônica: {e}")
                    assinatura_url = None

            context = {
                'oficio': oficio,
                'conta': oficio.conta,
                'brasao_url': brasao_uri, # Passamos o caminho file://
                'assinatura_url': assinatura_url, # URL da assinatura eletrônica (se disponível)
            }

            html_string = render_to_string('oficios/oficio_template.html', context)
            
            # O base_url aponta para a raiz do projeto para carregar CSS locais se necessário
            pdf_file = HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf()

            response = HttpResponse(pdf_file, content_type='application/pdf')
            # Limpa o número para não quebrar o nome do arquivo (remove barras)
            nome_limpo = oficio.numero.replace("/", "-").replace(" ", "_")
            response['Content-Disposition'] = f'attachment; filename="oficio_{nome_limpo}.pdf"'
            
            return response

        except Exception as e:
            traceback.print_exc()
            return Response(
                {"detail": f"Ocorreu um erro interno ao gerar o PDF: {e}"},
                status=500
            )

class TramitacaoAgendaViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gerenciar as tramitações de uma Solicitação de Agenda.
    """
    serializer_class = TramitacaoAgendaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """
        Filtra as tramitações com base na solicitação de agenda 
        fornecida como parâmetro na URL (ex: /api/tramitacoes-agenda/?solicitacao=1)
        """
        queryset = TramitacaoAgenda.objects.all().order_by('-data_tramitacao')
        solicitacao_id = self.request.query_params.get('solicitacao')
        if solicitacao_id:
            queryset = queryset.filter(solicitacao_id=solicitacao_id)
        # Garante que o usuário só veja tramitações das suas contas
        elif not self.request.user.is_superuser and hasattr(self.request.user, 'perfil'):
             contas_usuario = self.request.user.perfil.contas.all()
             queryset = queryset.filter(solicitacao__conta__in=contas_usuario)
        elif not self.request.user.is_superuser:
            return queryset.none() # Retorna nada se não for superuser e não tiver perfil
            
        return queryset

    def perform_create(self, serializer):
        """
        Associa automaticamente o usuário logado à tramitação criada.
        """
        serializer.save(usuario=self.request.user)

# -----------------------------------------------------------------------------
# Views de BI / Analytics (Revisado)
# -----------------------------------------------------------------------------

def aplicar_filtros_bi(queryset, request):
    """Delega ao filtro unificado de BI (período com timezone-aware)."""
    from .reporting import queryset_bi_atendimentos

    ids = queryset_bi_atendimentos(request.user, request).values_list('pk', flat=True)
    return queryset.filter(pk__in=ids)

class RelatorioProdutividadeEquipeView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, *args, **kwargs):
        queryset = aplicar_filtros_bi(Atendimento.objects.all(), request)
        
        data = queryset.values(
            nome_responsavel=Coalesce(
                F('responsavel__first_name'), 
                F('responsavel__username'), 
                Value('Não Atribuído'),
                output_field=CharField()
            )
        ).annotate(total=Count('id')).order_by('-total')
        
        return Response(data)

class RelatorioTopSolicitantesView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, *args, **kwargs):
        queryset = aplicar_filtros_bi(Atendimento.objects.all(), request)
        
        data = queryset.values(
            nome=F('municipe__nome_completo')
        ).annotate(total=Count('id')).order_by('-total')[:10]
        
        return Response(data)

class RelatorioEvolucaoAtendimentosView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, *args, **kwargs):
        queryset = aplicar_filtros_bi(Atendimento.objects.all(), request)
        periodo = request.query_params.get('periodo', 'mensal')

        # Otimização: Pegamos apenas o campo necessário
        dates = queryset.values_list('data_criacao', flat=True)
        stats = defaultdict(int)
        
        for dt in dates:
            if not dt: continue # Prevenção contra datas nulas
            
            if periodo == 'diario':
                key = dt.strftime('%Y-%m-%d')
            else:
                key = dt.strftime('%Y-%m-01') # Agrupa pelo dia 1º do mês
            stats[key] += 1
            
        sorted_keys = sorted(stats.keys())
        data = [{'data_ref': k, 'total': stats[k]} for k in sorted_keys]
        return Response(data)
    
class RelatorioStatusAtendimentosView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = aplicar_filtros_bi(Atendimento.objects.all(), request)
        
        data = queryset.values('status').annotate(total=Count('id')).order_by('-total')
        status_map = dict(Atendimento.STATUS_CHOICES)
        
        resultado = []
        for item in data:
            resultado.append({
                'status_code': item['status'],
                'label': status_map.get(item['status'], item['status']),
                'total': item['total']
            })
            
        return Response(resultado)


def aplicar_filtros_bi_visitas(queryset, request):
    """
    Reaproveita a mesma lógica de filtros do BI para registros de visita/check-in.
    """
    user = request.user

    # 1. Segurança por escopo de conta
    if user.is_superuser:
        queryset = queryset.all()
    elif hasattr(user, 'perfil'):
        queryset = queryset.filter(conta_destino__in=user.perfil.contas.all())
    else:
        return queryset.none()

    # 2. Filtros de data
    data_inicio = request.query_params.get('data_inicio')
    data_fim = request.query_params.get('data_fim')
    if data_inicio:
        queryset = queryset.filter(data_checkin__gte=f'{data_inicio} 00:00:00')
    if data_fim:
        queryset = queryset.filter(data_checkin__lte=f'{data_fim} 23:59:59')

    # 3. Filtro de conta específica
    conta_id = request.query_params.get('conta_id')
    if conta_id:
        queryset = queryset.filter(conta_destino_id=conta_id)

    # 4. Filtro por atendente/usuário
    usuario_id = request.query_params.get('usuario_id')
    if usuario_id:
        queryset = queryset.filter(registrado_por_id=usuario_id)
    elif request.query_params.get('apenas_meus') == 'true':
        queryset = queryset.filter(registrado_por=user)

    return queryset


class RelatorioBiAtendimentosPorAssuntoView(APIView):
    """Distribuição de atendimentos por assunto (substitui métricas legadas de check-in no BI)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        from .reporting import serializar_atendimentos_por_assunto

        qs = aplicar_filtros_bi(Atendimento.objects.all(), request)
        return Response(serializar_atendimentos_por_assunto(qs, top=12))


class RelatorioVisitasVolumeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = aplicar_filtros_bi_visitas(RegistroVisita.objects.all(), request)
        return Response({"total": queryset.count()})


class RelatorioVisitasTopTrendsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = aplicar_filtros_bi_visitas(RegistroVisita.objects.all(), request).only('data_checkin')
        volumes_por_dia = defaultdict(int)
        for visita in queryset:
            if not visita.data_checkin:
                continue
            dia_local = timezone.localtime(visita.data_checkin).date()
            volumes_por_dia[dia_local] += 1

        top_10 = sorted(volumes_por_dia.items(), key=lambda x: x[1], reverse=True)[:10]
        data = [
            {
                "data_ref": dia.strftime('%Y-%m-%d'),
                "label": dia.strftime('%d/%m/%Y'),
                "total": total,
            }
            for dia, total in top_10
        ]
        return Response(data)


class RelatorioVisitasTopAtendentesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = aplicar_filtros_bi_visitas(RegistroVisita.objects.all(), request)
        data = queryset.values(
            nome_destino=Coalesce(
                F('usuario_destino__first_name'),
                F('usuario_destino__username'),
                Value('Sem destino'),
                output_field=CharField()
            )
        ).annotate(total=Count('id')).order_by('-total')[:10]
        return Response(data)
    
class GerarRelatorioBiPdfView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:
            from .reporting import (
                parse_date_query_param,
                queryset_bi_atendimentos,
                resolver_logos_relatorio_pdf,
                serializar_atendimentos_por_assunto,
            )

            user = request.user
            qs_base = queryset_bi_atendimentos(user, request)

            total_atendimentos = qs_base.count()
            total_concluidos = qs_base.filter(status='CONCLUIDO').count()
            total_abertos = qs_base.filter(status='ABERTO').count()

            produtividade = list(
                qs_base.values(
                    nome=Coalesce(
                        F('responsavel__first_name'),
                        F('responsavel__username'),
                        Value('Não Atribuído'),
                        output_field=CharField(),
                    )
                ).annotate(qtd=Count('id')).order_by('-qtd')[:10]
            )

            solicitantes = list(
                qs_base.values(nome=F('municipe__nome_completo'))
                .annotate(qtd=Count('id'))
                .order_by('-qtd')[:10]
            )

            por_assunto = serializar_atendimentos_por_assunto(qs_base, top=12)

            conta_contexto = None
            conta_id = request.query_params.get('conta_id')
            if conta_id:
                conta_contexto = Conta.objects.filter(id=conta_id).first()
            elif hasattr(user, 'perfil') and user.perfil.contas.exists():
                conta_contexto = user.perfil.contas.first()

            logos = resolver_logos_relatorio_pdf(conta_contexto, request)
            data_inicio = parse_date_query_param(request.query_params.get('data_inicio'))
            data_fim = parse_date_query_param(request.query_params.get('data_fim'))
            apenas_meus = str(request.query_params.get('apenas_meus', '')).lower() in ('true', '1')

            context = {
                'data_inicio': data_inicio,
                'data_fim': data_fim,
                'periodo_label': (
                    f"{data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
                    if data_inicio and data_fim
                    else 'Período não informado'
                ),
                'filtro_usuario': request.query_params.get('usuario_id'),
                'usuario_emissao': user.get_full_name() or user.username,
                'data_emissao': timezone.localtime(),
                'apenas_meus': apenas_meus,
                'total_geral': total_atendimentos,
                'total_concluidos': total_concluidos,
                'total_abertos': total_abertos,
                'produtividade': produtividade,
                'solicitantes': solicitantes,
                'por_assunto': por_assunto,
                'titulo_relatorio': 'Relatório de Gestão e Produtividade',
                'sem_dados': total_atendimentos == 0,
                **logos,
            }

            html_string = render_to_string('relatorios/relatorio_bi.html', context)
            pdf_file = HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf()

            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="Relatorio_BI.pdf"'
            return response

        except Exception as e:
            import traceback
            traceback.print_exc() # Printa no terminal para ajudar a debugar
            return Response({'detail': f"Erro ao gerar PDF: {str(e)}"}, status=500)
        
# -----------------------------------------------------------------------------
# MODULO AGENDA CONVIDADOS / CONTROLE RECEPÇÃO
# -----------------------------------------------------------------------------

class AgendaInstitucionalViewSet(viewsets.ModelViewSet):
    serializer_class = AgendaCompromissoSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """
        Filtra compromissos unificando:
        1. Contas que o usuário pertence (Perfil).
        2. Contas que foram compartilhadas com ele (AgendaCompartilhamento).
        """
        user = self.request.user
        queryset = AgendaCompromisso.objects.prefetch_related('convidados__municipe__perfis')

        # 1. Filtro de Segurança Híbrido (Tenant + Compartilhamento)
        if not user.is_superuser:
            if hasattr(user, 'perfil'):
                # A) IDs das contas que sou dono/membro
                ids_minhas_contas = list(user.perfil.contas.values_list('id', flat=True))
                
                # B) IDs das contas compartilhadas comigo (via tabela AgendaCompartilhamento)
                # O 'related_name' no model AgendaCompartilhamento deve ser 'agendas_compartilhadas'
                ids_compartilhadas = list(user.agendas_compartilhadas.values_list('conta_alvo_id', flat=True))
                
                # C) União dos IDs (Set remove duplicados)
                ids_permitidos = set(ids_minhas_contas + ids_compartilhadas)
                
                queryset = queryset.filter(conta__in=ids_permitidos)
            else:
                return queryset.none()

        # 2. Filtro de URL (Query Params) - Mantido igual
        data_filtro = self.request.query_params.get('data')
        if data_filtro:
            target_date = parse_date(data_filtro)
            if target_date:
                current_tz = timezone.get_current_timezone()
                start_of_day = timezone.make_aware(datetime.combine(target_date, time.min), current_tz)
                end_of_day = timezone.make_aware(datetime.combine(target_date, time.max), current_tz)
                queryset = queryset.filter(data_inicio__range=(start_of_day, end_of_day))
            
        conta_id = self.request.query_params.get('conta_id')
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)

        return queryset.order_by('data_inicio')

    def verificar_permissao_escrita(self, conta):
        """
        Helper para verificar se o usuário pode ESCREVER nesta conta.
        """
        user = self.request.user
        if user.is_superuser:
            return True
            
        # 1. É conta do meu perfil?
        if conta in user.perfil.contas.all():
            return True
            
        # 2. Tenho compartilhamento com nível ESCRITA?
        # Verifica na tabela AgendaCompartilhamento
        tem_permissao = user.agendas_compartilhadas.filter(
            conta_alvo=conta, 
            nivel='ESCRITA'
        ).exists()
        
        return tem_permissao

    def perform_create(self, serializer):
        """
        Ao criar, verifica se tenho permissão de escrita na conta alvo.
        """
        conta_alvo = serializer.validated_data['conta']
        
        if not self.verificar_permissao_escrita(conta_alvo):
            raise serializers.ValidationError(
                {"detail": "Você tem acesso apenas de LEITURA à agenda desta conta."}
            )
            
        serializer.save(criado_por=self.request.user)

    def perform_update(self, serializer):
        """
        Ao editar, também verifica permissão de escrita.
        """
        # O objeto já existe, pegamos a conta dele
        compromisso = self.get_object()
        
        # Nota: Se o usuário tentar mudar a conta do compromisso no PUT, 
        # deveríamos validar a nova conta também. Assumindo que a conta não muda:
        if not self.verificar_permissao_escrita(compromisso.conta):
            raise serializers.ValidationError(
                {"detail": "Você tem acesso apenas de LEITURA à agenda desta conta."}
            )
            
        serializer.save()

    def perform_destroy(self, instance):
        """
        Ao excluir, verifica permissão.
        """
        if not self.verificar_permissao_escrita(instance.conta):
            raise serializers.ValidationError(
                {"detail": "Você tem acesso apenas de LEITURA à agenda desta conta."}
            )
        instance.delete()

    # --- Actions de Convidados (Mantidas iguais) ---
    @action(detail=True, methods=['post'])
    def adicionar_convidado(self, request, pk=None):
        compromisso = self.get_object()
        
        # Validar permissão de escrita também aqui
        if not self.verificar_permissao_escrita(compromisso.conta):
             return Response({'detail': 'Sem permissão de escrita.'}, status=403)

        municipe_id = request.data.get('municipe_id')
        observacao = request.data.get('observacao', '')

        if not municipe_id:
            return Response({'detail': 'ID do munícipe é obrigatório.'}, status=400)

        try:
            AgendaConvidado.objects.create(
                compromisso=compromisso,
                municipe_id=municipe_id,
                observacao=observacao
            )
            serializer = self.get_serializer(compromisso)
            return Response(serializer.data)
        except Exception as e:
            return Response({'detail': f'Erro ao adicionar convidado: {str(e)}'}, status=400)
        
    @action(detail=True, methods=['post'])
    def remover_convidado(self, request, pk=None):
        compromisso = self.get_object()
        
        # Validar permissão
        if not self.verificar_permissao_escrita(compromisso.conta):
             return Response({'detail': 'Sem permissão de escrita.'}, status=403)

        convidado_id = request.data.get('convidado_id')

        if not convidado_id:
            return Response({'detail': 'ID do convidado é obrigatório.'}, status=400)

        deleted_count, _ = AgendaConvidado.objects.filter(
            id=convidado_id, 
            compromisso=compromisso
        ).delete()

        if deleted_count == 0:
            return Response({'detail': 'Convidado não encontrado ou já removido.'}, status=404)

        serializer = self.get_serializer(compromisso)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def minhas_agendas(self, request):
        user = request.user
        data = []

        # 1. Contas que eu sou DONO (Permissão Total)
        if hasattr(user, 'perfil'):
            minhas_contas = user.perfil.contas.all()
            for conta in minhas_contas:
                data.append({
                    'id': conta.id,
                    'nome': conta.nome, # ou nome_instituicao
                    'permissao': 'ESCRITA' # Sou dono
                })

        # 2. Contas Compartilhadas comigo (Verificar Nível)
        compartilhamentos = user.agendas_compartilhadas.select_related('conta_alvo').all()
        for share in compartilhamentos:
            # Evita duplicar se eu for dono e compartilharam comigo mesmo (borda)
            if not any(d['id'] == share.conta_alvo.id for d in data):
                data.append({
                    'id': share.conta_alvo.id,
                    'nome': share.conta_alvo.nome,
                    'permissao': share.nivel # 'LEITURA' ou 'ESCRITA'
                })
        
        # Se for superuser, vê tudo como escrita (opcional)
        if user.is_superuser:
            # ... lógica para superuser (opcional, pode manter a acima para simplicidade)
            pass

        return Response(data)

class AgendaConvidadoCheckinView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            # Verifica segurança antes
            user = request.user
            convidado = AgendaConvidado.objects.select_related('compromisso').get(pk=pk)
            
            # Valida se o usuário tem acesso à conta desse compromisso
            if not user.is_superuser and hasattr(user, 'perfil'):
                if convidado.compromisso.conta not in user.perfil.contas.all():
                    return Response({'detail': 'Acesso negado a esta agenda.'}, status=403)

            # Toggle Check-in
            if convidado.chegou:
                convidado.chegou = False
                convidado.horario_chegada = None
            else:
                convidado.chegou = True
                convidado.horario_chegada = datetime.now()
            
            convidado.save()
            return Response({'status': 'ok', 'chegou': convidado.chegou, 'horario': convidado.horario_chegada})
            
        except AgendaConvidado.DoesNotExist:
            return Response({'detail': 'Convidado não encontrado'}, status=404)
        
class AgendaCompartilhamentoView(viewsets.ModelViewSet):
    """
    View para gerenciar quem pode ver qual agenda.
    Endpoint final será: /api/agenda-compartilhamentos/
    """
    serializer_class = AgendaCompartilhamentoSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'perfil'):
            minhas_contas = user.perfil.contas.all()
            return AgendaCompartilhamento.objects.filter(conta_alvo__in=minhas_contas)
        return AgendaCompartilhamento.objects.none()

    def perform_create(self, serializer):
        conta_alvo = serializer.validated_data['conta_alvo']
        user = self.request.user
        if not user.is_superuser and conta_alvo not in user.perfil.contas.all():
            raise serializers.ValidationError("Sem permissão para compartilhar esta conta.")
        serializer.save(criado_por=user)


# ========================================
# VIEWSETS MÚLTIPLAS CONTAS GOOGLE - FASE 3
# ========================================

class ContaGoogleCalendarViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet para listar contas Google Calendar disponíveis para o usuário.
    
    Endpoints:
    - GET /api/contas-google/ - Lista contas disponíveis
    - GET /api/contas-google/{id}/ - Detalhes de uma conta
    - GET /api/contas-google/status/ - Status consolidado de todas as contas
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get_serializer_class(self):
        # Usar sempre o serializer completo que inclui client_id
        return ContaGoogleCalendarSerializer
    
    def get_queryset(self):
        """Contas Google que o usuário pode visualizar (inclui leitores SIGA)."""
        user = self.request.user

        if not user.is_authenticated:
            return ContaGoogleCalendar.objects.none()

        qs = ContaGoogleCalendar.objects.filter(
            ativa=True,
            permissoes_usuarios__usuario=user,
            permissoes_usuarios__pode_visualizar=True,
        )

        try:
            contas_perfil = user.perfil.contas.values_list('id', flat=True)
            if contas_perfil:
                qs = qs.filter(conta_id__in=contas_perfil)
        except Exception:
            pass

        return qs.distinct().select_related('conta').prefetch_related('permissoes_usuarios')
    
    @action(detail=False, methods=['get'])
    def status(self, request):
        """
        Endpoint consolidado com status de todas as contas Google do usuário.

        GET /api/contas-google/status/

        Retorna a mesma estrutura de /api/contas-google/ com token_status em objeto
        e authorization_url quando necessário.
        """
        from .serializers import build_token_status_payload

        contas = self.get_queryset()
        result = []

        for conta_google in contas:
            try:
                permissao = UsuarioContaGooglePermissao.objects.get(
                    usuario=request.user,
                    conta_google=conta_google
                )
            except UsuarioContaGooglePermissao.DoesNotExist:
                continue

            from .services.google_calendar_compatibility import GoogleCalendarCompatibilityService
            token_status = GoogleCalendarCompatibilityService.obter_status_token_usuario(
                request.user, conta_google
            )

            permissoes_usuario = {
                'pode_visualizar': permissao.pode_visualizar,
                'pode_criar': permissao.pode_criar,
                'pode_editar': permissao.pode_editar,
                'pode_excluir': permissao.pode_excluir,
                'nivel_acesso': permissao.nivel_acesso,
            }

            authorization_url = None
            if token_status['precisa_autorizacao']:
                try:
                    from .services import GoogleCalendarCompatibilityService
                    redirect_uri = request.build_absolute_uri(
                        f'/api/google-calendar/auth/{conta_google.id}/callback/'
                    )
                    auth_url, _state = GoogleCalendarCompatibilityService.get_auth_url(
                        conta_google.id,
                        redirect_uri
                    )
                    authorization_url = auth_url
                except Exception:
                    authorization_url = None

            result.append({
                'id': conta_google.id,
                'nome': conta_google.nome,
                'descricao': conta_google.descricao or '',
                'email_google': conta_google.email_google,
                'eh_padrao': conta_google.eh_padrao,
                'conta_nome': conta_google.conta.nome,
                'calendar_id': conta_google.calendar_id or '',
                'client_id': conta_google.get_client_id() or '',
                'total_usuarios': conta_google.permissoes_usuarios.filter(
                    pode_visualizar=True
                ).count(),
                'permissoes_usuario': permissoes_usuario,
                'token_status': token_status,
                'pode_visualizar': permissao.pode_visualizar,
                'pode_criar': permissao.pode_criar,
                'pode_editar': permissao.pode_editar,
                'pode_excluir': permissao.pode_excluir,
                'nivel_acesso': permissao.nivel_acesso,
                'dias_para_expirar': token_status['dias_para_expirar'],
                'precisa_autorizacao': token_status['precisa_autorizacao'],
                'authorization_url': authorization_url,
            })

        serializer = GoogleAccountStatusSerializer(result, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def permissions(self, request, pk=None):
        """
        Retorna permissões detalhadas do usuário para uma conta Google específica.
        
        GET /api/contas-google/{id}/permissions/
        """
        conta_google = self.get_object()
        
        try:
            permissao = UsuarioContaGooglePermissao.objects.get(
                usuario=request.user,
                conta_google=conta_google
            )
            serializer = UsuarioContaGooglePermissaoSerializer(permissao)
            return Response(serializer.data)
        except UsuarioContaGooglePermissao.DoesNotExist:
            return Response(
                {'error': 'Usuário não tem permissão para esta conta Google'}, 
                status=status.HTTP_403_FORBIDDEN
            )
    
    @action(detail=False, methods=['get'])
    def debug(self, request):
        """Endpoint de debug para verificar dados das contas"""
        try:
            user = request.user
            
            # Verificar PerfilUsuario
            try:
                perfil = PerfilUsuario.objects.get(usuario=user)
                perfil_info = {
                    'exists': True,
                    'contas': [c.nome for c in perfil.contas.all()],
                    'total_contas': perfil.contas.count()
                }
            except PerfilUsuario.DoesNotExist:
                perfil_info = {'exists': False}
            
            # Verificar lógica _get_conta_usuario
            conta_usuario = self._get_conta_usuario(user)
            conta_usuario_info = {
                'nome': conta_usuario.nome if conta_usuario else None,
                'id': conta_usuario.id if conta_usuario else None
            }
            
            # Verificar contas disponíveis com queryset completo
            contas = self.get_queryset()
            
            debug_data = []
            for conta in contas:
                debug_data.append({
                    'id': conta.id,
                    'nome': conta.nome,
                    'client_id': conta.client_id,
                    'client_secret_configured': bool(conta.client_secret),
                    'ativa': conta.ativa,
                    'email_google': conta.email_google
                })
            
            # Verificar ALL contas (sem filtro de permissão)
            todas_contas = ContaGoogleCalendar.objects.all()
            all_contas_data = []
            for conta in todas_contas:
                all_contas_data.append({
                    'id': conta.id,
                    'nome': conta.nome,
                    'client_id': conta.client_id,
                    'ativa': conta.ativa
                })
            
            return Response({
                'user': user.username,
                'authenticated': user.is_authenticated,
                'perfil': perfil_info,
                'conta_usuario': conta_usuario_info,
                'contas_google_com_permissao': debug_data,
                'total_contas_com_permissao': len(debug_data),
                'all_contas_google': all_contas_data,
                'serializer_class': self.get_serializer_class().__name__
            })
            
        except Exception as e:
            import traceback
            return Response({
                'error': str(e),
                'traceback': traceback.format_exc(),
                'user': request.user.username if hasattr(request, 'user') else 'No user',
                'authenticated': request.user.is_authenticated if hasattr(request, 'user') else False
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def simple(self, request):
        """Endpoint simplificado que sempre retorna as contas com client_id"""
        try:
            # Retornar todas as contas diretamente (para debug)
            contas = ContaGoogleCalendar.objects.filter(ativa=True).order_by('id')
            
            result = []
            for conta in contas:
                result.append({
                    'id': conta.id,
                    'nome': conta.nome,
                    'descricao': conta.descricao,
                    'email_google': conta.email_google,
                    'client_id': conta.client_id,
                    'client_secret_configured': bool(conta.client_secret),
                    'ativa': conta.ativa,
                    'eh_padrao': conta.eh_padrao,
                    'calendar_id': conta.calendar_id,
                    # Verificar se tem token
                    'is_connected': TokenGoogleCalendar.objects.filter(
                        usuario=request.user,
                        conta_google=conta
                    ).exists()
                })
            
            return Response({
                'results': result,
                'count': len(result),
                'user': request.user.username
            })
            
        except Exception as e:
            import traceback
            return Response({
                'error': str(e),
                'traceback': traceback.format_exc(),
                'user': request.user.username if hasattr(request, 'user') else 'No user',
                'authenticated': request.user.is_authenticated if hasattr(request, 'user') else False
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _get_conta_usuario(self, user):
        """
        Retorna a conta/gabinete associada ao usuário.
        🚨 IMPLEMENTAR baseado na lógica do seu sistema!
        """
        # Abordagem 1: Profile com conta
        if hasattr(user, 'perfil') and hasattr(user.perfil, 'contas'):
            return user.perfil.contas.first()
        
        # Abordagem 2: Grupos do Django
        grupos_usuario = user.groups.values_list('name', flat=True)
        for grupo_nome in grupos_usuario:
            conta = Conta.objects.filter(nome__icontains=grupo_nome).first()
            if conta:
                return conta
        
        # Fallback: Primeira conta com Google configurado
        return Conta.objects.exclude(
            google_calendar_id__isnull=True
        ).exclude(
            google_calendar_id__exact=''
        ).first()


class GoogleCalendarAuthViewSet(viewsets.ViewSet):
    """
    ViewSet para autenticação OAuth com contas Google específicas.
    
    Endpoints:
    - POST /api/google-calendar/auth/start/ - Inicia OAuth
    - GET /api/google-calendar/auth/{id}/callback/ - Callback OAuth
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get_permissions(self):
        """
        Allow unauthenticated access to callback endpoints.
        """
        if self.action in ['callback', 'callback_generic']:
            return []
        return super().get_permissions()
    
    @action(detail=False, methods=['post'])
    def start(self, request):
        """
        Inicia processo OAuth para uma conta Google específica.
        
        POST /api/google-calendar/auth/start/
        Body: {
            "conta_google_id": 1,
            "redirect_uri": "https://..." (opcional)
        }
        """
        serializer = OAuthInitiationSerializer(
            data=request.data, 
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        
        conta_google_id = serializer.validated_data['conta_google_id']
        
        # VERIFICAÇÃO DE PERMISSÃO: Flexível para conta principal
        try:
            permissao = UsuarioContaGooglePermissao.objects.get(
                usuario=request.user,
                conta_google_id=conta_google_id,
                conta_google__ativa=True
            )
            
            # Para conta ID 1 (Agenda Principal): qualquer usuário com permissão pode autorizar
            # Para outras contas: apenas quem tem pode_editar
            if conta_google_id == 1:
                # Conta principal - qualquer usuário com acesso pode autorizar
                if not permissao.pode_visualizar:
                    return Response(
                        {'error': 'Usuário não tem acesso a esta conta Google'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            else:
                # Contas específicas - apenas gestores
                if not permissao.pode_editar:
                    return Response(
                        {'error': 'Apenas gestores podem autorizar esta conta Google específica'},
                        status=status.HTTP_403_FORBIDDEN
                    )
        except UsuarioContaGooglePermissao.DoesNotExist:
            return Response(
                {'error': 'Usuário não tem permissão para esta conta Google'},
                status=status.HTTP_403_FORBIDDEN
            )
        redirect_uri = serializer.validated_data.get(
            'redirect_uri',
            request.build_absolute_uri(f'/api/google-calendar/auth/{conta_google_id}/callback/')
        )
        
        try:
            from .services import GoogleCalendarCompatibilityService
            auth_url, state = GoogleCalendarCompatibilityService.get_auth_url(
                conta_google_id, 
                redirect_uri
            )
            
            # Salva estado na sessão
            request.session[f'google_oauth2_state_{conta_google_id}'] = state
            request.session[f'google_auth_user_id_{conta_google_id}'] = request.user.id
            
            return Response({
                'authorization_url': auth_url,
                'state': state,
                'conta_google_id': conta_google_id
            })
            
        except Exception as e:
            return Response(
                {'error': f'Erro ao gerar URL de autorização: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def callback_generic(self, request):
        """
        Callback genérico que detecta a conta pelo client_id.
        
        GET /api/google-calendar/auth/callback/?code=...&state=...&client_id=...
        """
        code = request.query_params.get('code')
        state = request.query_params.get('state')
        
        if not code:
            return Response(
                {'error': 'Código de autorização não fornecido'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Tentar detectar a conta pelo referrer ou usar a primeira conta ativa
        try:
            # Por simplicidade, vamos usar a conta ID 1 como padrão
            # TODO: Melhorar a detecção da conta
            return self.callback(request, pk='1')
        except Exception as e:
            return Response(
                {'error': f'Erro no callback: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'])
    def callback(self, request, pk=None):
        """
        Processa callback OAuth e salva tokens.
        
        GET /api/google-calendar/auth/{conta_google_id}/callback/?code=...&state=...
        """
        conta_google_id = pk
        state = request.query_params.get('state')
        code = request.query_params.get('code')
        
        if not code:
            return Response(
                {'error': 'Código de autorização é obrigatório'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verifica estado da sessão (opcional para autorização direta)
        if state:
            session_state = request.session.get(f'google_oauth2_state_{conta_google_id}')
            if session_state and state != session_state:
                return Response(
                    {'error': 'State mismatch - possível ataque CSRF'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Verifica usuário (da sessão ou usuário autenticado)
        user_id = request.session.get(f'google_auth_user_id_{conta_google_id}')
        user = None
        
        # Se há usuário na sessão, usá-lo
        if user_id:
            try:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                user = User.objects.get(pk=user_id)
            except User.DoesNotExist:
                return Response(
                    {'error': 'Usuário da sessão não encontrado'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Se não há usuário na sessão mas há usuário autenticado, usá-lo
        elif request.user and request.user.is_authenticated:
            user = request.user
            user_id = user.id
        
        # Se não há usuário nem na sessão nem autenticado, erro
        else:
            # Para autorização direta, vamos permitir sem usuário específico
            # e usar um usuário padrão (admin) para processar o token
            try:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                user = User.objects.get(is_superuser=True)  # Usar primeiro superusuário
                user_id = user.id
            except User.DoesNotExist:
                return Response(
                    {'error': 'Não foi possível processar a autorização - nenhum usuário disponível'}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        
        try:
            from .services import GoogleCalendarCompatibilityService
            
            # Processa callback e salva token
            token = GoogleCalendarCompatibilityService.process_oauth_callback(
                int(conta_google_id),
                request.build_absolute_uri(),
                user
            )
            
            # Limpa sessão
            request.session.pop(f'google_oauth2_state_{conta_google_id}', None)
            request.session.pop(f'google_auth_user_id_{conta_google_id}', None)
            
            # Retorna página de sucesso que fecha popup
            html_response = '''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Autorização Concluída</title>
                <style>
                    body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                    .success { color: #28a745; font-size: 24px; }
                    .info { color: #6c757d; margin-top: 20px; }
                </style>
            </head>
            <body>
                <div class="success">✅ Autorização concluída com sucesso!</div>
                <div class="info">Esta janela será fechada automaticamente.</div>
                <script>
                    // Envia mensagem para a janela pai
                    if (window.opener) {
                        window.opener.postMessage({
                            type: 'GOOGLE_AUTH_SUCCESS',
                            conta_google_id: ''' + str(conta_google_id) + '''
                        }, '*');
                    }
                    // Fecha a janela após 2 segundos
                    setTimeout(function() {
                        window.close();
                    }, 2000);
                </script>
            </body>
            </html>
            '''
            
            return HttpResponse(html_response, content_type='text/html')
            
        except Exception as e:
            return Response(
                {'error': f'Erro ao processar callback: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GoogleCalendarEventViewSet(viewsets.ViewSet):
    """
    ViewSet para operações com eventos do Google Calendar.
    
    Endpoints:
    - POST /api/google-calendar/events/ - Criar evento
    - GET /api/google-calendar/events/{conta_google_id}/ - Listar eventos
    """
    permission_classes = [permissions.IsAuthenticated]
    
    @action(detail=False, methods=['post'])
    def create_event(self, request):
        """
        Cria evento no Google Calendar da conta específica.
        
        POST /api/google-calendar/events/
        Body: {
            "conta_google_id": 1,
            "titulo": "Reunião importante",
            "descricao": "Descrição do evento",
            "data_inicio": "2026-05-27T10:00:00Z",
            "data_fim": "2026-05-27T11:00:00Z",
            "local": "Sala de reuniões"
        }
        """
        serializer = EventCreationSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        
        try:
            from .services import GoogleCalendarCompatibilityService
            
            conta_id = serializer.validated_data['conta_google_id']
            if not GoogleCalendarCompatibilityService.usuario_pode_criar_evento(
                request.user, conta_id
            ):
                return Response(
                    {'error': 'Sem permissão para criar eventos nesta agenda.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

            service = GoogleCalendarCompatibilityService.get_calendar_service(
                request.user,
                conta_id,
                requer_token_proprio=True,
            )
            
            # Busca conta Google para pegar calendar_id
            conta_google = ContaGoogleCalendar.objects.get(
                id=serializer.validated_data['conta_google_id']
            )
            
            # Prepara evento
            event_data = {
                'summary': serializer.validated_data['titulo'],
                'description': serializer.validated_data.get('descricao', ''),
                'start': {
                    'dateTime': serializer.validated_data['data_inicio'].isoformat(),
                    'timeZone': 'America/Sao_Paulo',
                },
                'end': {
                    'dateTime': serializer.validated_data['data_fim'].isoformat(),
                    'timeZone': 'America/Sao_Paulo',
                },
            }
            
            if serializer.validated_data.get('local'):
                event_data['location'] = serializer.validated_data['local']
            
            # Cria evento no Google Calendar
            created_event = service.events().insert(
                calendarId=conta_google.calendar_id,
                body=event_data
            ).execute()
            
            return Response({
                'success': True,
                'event_id': created_event['id'],
                'event_link': created_event['htmlLink'],
                'message': f'Evento criado com sucesso na {conta_google.nome}'
            })
            
        except Exception as e:
            return Response(
                {'error': f'Erro ao criar evento: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['patch'], url_path='update_google_event')
    def update_google_event(self, request):
        """
        Atualiza evento no Google Calendar da conta selecionada.

        PATCH /api/google-calendar/events/update_google_event/
        Body: {
            "event_id": "...",
            "conta_google_id": 1,
            "titulo": "...",
            "data_inicio": "...",
            "data_fim": "..."
        }
        """
        from .serializers import EventUpdateSerializer

        serializer = EventUpdateSerializer(
            data=request.data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)

        try:
            from .services import GoogleCalendarCompatibilityService

            conta_id = serializer.validated_data['conta_google_id']
            conta_google = ContaGoogleCalendar.objects.get(id=conta_id, ativa=True)
            service = GoogleCalendarCompatibilityService.get_calendar_service(
                request.user,
                conta_id,
                requer_token_proprio=True,
            )

            event_body = {
                'summary': serializer.validated_data['titulo'],
                'description': serializer.validated_data.get('descricao', ''),
                'start': {
                    'dateTime': serializer.validated_data['data_inicio'].isoformat(),
                    'timeZone': 'America/Sao_Paulo',
                },
                'end': {
                    'dateTime': serializer.validated_data['data_fim'].isoformat(),
                    'timeZone': 'America/Sao_Paulo',
                },
            }
            if serializer.validated_data.get('local'):
                event_body['location'] = serializer.validated_data['local']

            updated = service.events().patch(
                calendarId=conta_google.calendar_id,
                eventId=str(serializer.validated_data['event_id']),
                body=event_body,
            ).execute()

            return Response({
                'success': True,
                'event_id': updated.get('id'),
                'message': f'Evento atualizado na {conta_google.nome}',
            })
        except HttpError as error:
            status_code = error.resp.status if error.resp else status.HTTP_400_BAD_REQUEST
            return Response(
                {'error': f'Falha ao atualizar evento no Google Calendar: {error}'},
                status=status_code,
            )
        except Exception as e:
            return Response(
                {'error': f'Erro ao atualizar evento: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=['delete'], url_path='delete_google_event')
    def delete_google_event(self, request):
        """
        Exclui evento no Google Calendar da conta selecionada.

        DELETE /api/google-calendar/events/delete_google_event/
        Body: { "event_id": "...", "conta_google_id": 1 }
        """
        event_id = request.data.get('event_id')
        conta_google_id = request.data.get('conta_google_id')

        if not event_id or not conta_google_id:
            return Response(
                {'error': 'event_id e conta_google_id são obrigatórios.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            conta_google_id = int(conta_google_id)
        except (TypeError, ValueError):
            return Response(
                {'error': 'conta_google_id inválido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .services import GoogleCalendarCompatibilityService

        if not GoogleCalendarCompatibilityService.usuario_pode_visualizar_eventos(
            request.user, conta_google_id
        ):
            return Response(
                {'error': 'Sem permissão para excluir eventos nesta agenda.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            permissao = UsuarioContaGooglePermissao.objects.get(
                usuario=request.user,
                conta_google_id=conta_google_id,
            )
        except UsuarioContaGooglePermissao.DoesNotExist:
            return Response(
                {'error': 'Sem permissão para excluir eventos nesta agenda.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not permissao.pode_excluir:
            return Response(
                {'error': 'Sem permissão para excluir eventos nesta agenda.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            conta_google = ContaGoogleCalendar.objects.get(id=conta_google_id, ativa=True)
            service = GoogleCalendarCompatibilityService.get_calendar_service(
                request.user,
                conta_google_id,
                requer_token_proprio=False,
            )
            service.events().delete(
                calendarId=conta_google.calendar_id,
                eventId=str(event_id),
            ).execute()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ContaGoogleCalendar.DoesNotExist:
            return Response(
                {'error': 'Conta Google não encontrada ou inativa.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except HttpError as error:
            status_code = error.resp.status if error.resp else status.HTTP_400_BAD_REQUEST
            detail = (
                'Evento não encontrado nesta agenda. Verifique se a conta selecionada '
                'é a mesma em que o evento foi criado.'
                if status_code == 404
                else f'Falha ao excluir evento no Google Calendar: {error}'
            )
            return Response({'error': detail}, status=status_code)
        except Exception as e:
            return Response(
                {'error': f'Erro ao excluir evento: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    
    @action(detail=False, methods=['get'])
    def list_events(self, request):
        """
        Lista eventos de uma conta Google específica.
        
        GET /api/google-calendar/events/list_events/?conta_google_id=1&start_date=2026-05-01&end_date=2026-05-31
        """
        try:
            conta_google_id = request.GET.get('conta_google_id')
            if not conta_google_id:
                return Response(
                    {'error': 'conta_google_id é obrigatório'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            conta_google_id = int(conta_google_id)
            
            from .services import GoogleCalendarCompatibilityService
            
            # Obtém o usuário (pode vir de request.user ou self.request.user)
            user = getattr(request, 'user', None) or getattr(self.request, 'user', None)
            if not user:
                return Response(
                    {'error': 'Usuário não autenticado'},
                    status=status.HTTP_401_UNAUTHORIZED
                )
            
            logger.info(f"🔍 DEBUG list_events - Usuário: {user.username} (ID: {user.id}), Conta: {conta_google_id}")
            
            # Verifica permissão (para listar eventos, só precisa de visualizar)
            if not GoogleCalendarCompatibilityService.usuario_pode_visualizar_eventos(
                user, conta_google_id
            ):
                return Response(
                    {'error': 'Usuário não tem permissão para visualizar eventos desta conta Google'}, 
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Busca conta Google
            try:
                conta_google = ContaGoogleCalendar.objects.get(id=conta_google_id)
            except ContaGoogleCalendar.DoesNotExist:
                return Response(
                    {'error': 'Conta Google não encontrada'}, 
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Verifica se tem credenciais configuradas
            if not conta_google.client_id or not conta_google.client_secret:
                return Response({
                    'events': [],
                    'message': f'Conta "{conta_google.nome}" precisa ser configurada no Django Admin',
                    'config_needed': True
                })
            
            # Busca serviço
            try:
                logger.info(f"🔍 Tentando obter serviço para usuário {user.username} (ID: {user.id}) e conta {conta_google_id}")
                service = GoogleCalendarCompatibilityService.get_calendar_service(
                    user, conta_google_id
                )
                logger.info(f"✅ Serviço Google Calendar obtido com sucesso para usuário {user.username} e conta {conta_google_id}")
            except Exception as e:
                logger.error(f"❌ Erro ao obter serviço Google Calendar para {user.username}: {str(e)}")
                logger.error(f"🔍 Tipo do erro: {type(e).__name__}")
                return Response({
                    'events': [],
                    'message': f'Erro ao conectar com Google Calendar: Usuário precisa fazer autenticação OAuth para: {conta_google.nome}',
                    'auth_needed': True,
                    'error_details': str(e),
                    'debug_info': {
                        'user_id': user.id,
                        'user_username': user.username,
                        'conta_google_id': conta_google_id,
                        'error_type': type(e).__name__
                    }
                })
            
            # Parâmetros de data (compatível com Django testing)
            query_params = getattr(request, 'query_params', request.GET)
            start_date = query_params.get('start_date')
            end_date = query_params.get('end_date')
            
            # Busca eventos
            try:
                logger.info(f"Buscando eventos para conta {conta_google.nome} (calendar_id: {conta_google.calendar_id})")
                
                events_result = service.events().list(
                    calendarId=conta_google.calendar_id,
                    timeMin=start_date + 'T00:00:00Z' if start_date else None,
                    timeMax=end_date + 'T23:59:59Z' if end_date else None,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                
                events = events_result.get('items', [])
                logger.info(f"✅ Encontrados {len(events)} eventos para conta {conta_google.nome}")
                
                return Response({
                    'conta_google_nome': conta_google.nome,
                    'total_events': len(events),
                    'events': events,
                    'success': True
                })
                
            except Exception as events_error:
                logger.error(f"Erro específico ao buscar eventos: {str(events_error)}")
                return Response({
                    'events': [],
                    'message': f'Erro ao buscar eventos do Google Calendar: {str(events_error)}',
                    'auth_needed': False,
                    'error_details': str(events_error)
                })
            
        except Exception as e:
            return Response(
                {'error': f'Erro ao listar eventos: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )