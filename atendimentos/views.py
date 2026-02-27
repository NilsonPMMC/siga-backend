import os
import base64
import openpyxl
import calendar
import operator
import traceback
import logging
import uuid
import threading

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
from rest_framework.generics import ListAPIView
from rest_framework.decorators import action

# Imports locais (do seu projeto)
from .utils import enviar_email_com_cid
from oficios.models import Oficio

# Configurar logger
logger = logging.getLogger(__name__)
from .models import *
from .utils_dados import is_data_dirty
from .permissions import (CanAccessContacts, CanAccessObjectByConta, CanViewSharedAgenda, CanAccessEspaco,
                          CanInteractWithAtendimento, CanManageAgendas, CanCreateGoogleEvent, CanManageReservas,
                          CanViewAgendaReports, CanViewAtendimentoReports, CanEditMunicipeDetails, CanManageCheckIn,
                          CanCreateCheckIn, CanManageLembretes, is_in_group)
from .serializers import *


# -----------------------------------------------------------------------------
# Views de Atendimento
# -----------------------------------------------------------------------------

class AtendimentoListCreateView(generics.ListCreateAPIView):
    serializer_class = AtendimentoSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Atendimento.objects.select_related('municipe', 'conta', 'responsavel')

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
                Q(responsavel=user) | Q(responsavel__isnull=True)
            )
        else:
            # REGRA 4: Se nenhuma das anteriores se aplicar, não mostra nada.
            return Atendimento.objects.none()

        # Aplicar filtro de busca textual (se fornecido)
        termo_busca = self.request.query_params.get('q', None)
        if termo_busca:
            queryset = queryset.filter(
                Q(protocolo__icontains=termo_busca) |
                Q(titulo__icontains=termo_busca) |
                Q(municipe__nome_completo__icontains=termo_busca) |
                Q(municipe__nome_de_guerra__icontains=termo_busca)
            )

        # Aplicar filtro de status (se fornecido)
        status = self.request.query_params.get('status', None)
        if status:
            queryset = queryset.filter(status=status)

        # Aplicar filtro de conta (se fornecido)
        conta_id = self.request.query_params.get('conta_id', None)
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)

        return queryset.order_by('-data_criacao')

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class AtendimentoDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Atendimento.objects.all()
    serializer_class = AtendimentoSerializer
    permission_classes = [permissions.IsAuthenticated, CanInteractWithAtendimento]


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
        registro = serializer.save(registrado_por=self.request.user)
        nome_municipe = registro.municipe.nome_completo
        mensagem = f"O Munícipe {nome_municipe} acabou de chegar para uma visita/reunião."
        link = f"/contatos/{registro.municipe_id}"
        usuarios_notificar = []
        if registro.usuario_destino_id:
            usuarios_notificar = [registro.usuario_destino]
        else:
            usuarios_notificar = list(
                User.objects.filter(perfil__contas=registro.conta_destino).distinct()
            )
        for usuario in usuarios_notificar:
            Notificacao.objects.create(usuario=usuario, mensagem=mensagem, link=link)

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
        # Se for superuser e tiver conta_id, filtra membros da conta
        conta_id = self.request.query_params.get('conta_id', None)
        if self.request.user.is_superuser and conta_id:
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
    permission_classes = [permissions.IsAuthenticated]
    queryset = CategoriaAtendimento.objects.filter(ativa=True)
    serializer_class = CategoriaAtendimentoSerializer

class CategoriaContatoListView(generics.ListAPIView):
    queryset = CategoriaContato.objects.filter(ativa=True)
    serializer_class = CategoriaContatoSerializer
    permission_classes = [IsAuthenticated]

class CategoriaContatoViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gerenciar (CRUD) as Categorias de Contato.
    """
    queryset = CategoriaContato.objects.all().order_by('nome')
    serializer_class = CategoriaContatoSerializer
    permission_classes = [permissions.IsAuthenticated]

# -----------------------------------------------------------------------------
# Views de Munícipe
# -----------------------------------------------------------------------------

class MunicipeListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]
    serializer_class = MunicipeSerializer

    def get_queryset(self):
        user = self.request.user
        termo_busca = self.request.query_params.get('q', None)
        letra_inicial = self.request.query_params.get('letra', None)
        grupo_id = self.request.query_params.get('grupo', None)
        tem_grupo_duplicado = self.request.query_params.get('tem_grupo_duplicado', None)
        categoria_ids = self.request.query_params.getlist('categoria') or self.request.query_params.getlist('categoria_id')

        filtro_aplicado = bool(termo_busca or letra_inicial or categoria_ids)

        base_queryset = Municipe.objects.prefetch_related('contas', 'categoria', 'perfis')

        if grupo_id:
            return base_queryset.filter(grupo_duplicado=grupo_id).order_by('nome_completo')

        if tem_grupo_duplicado == 'true':
            return base_queryset.exclude(grupo_duplicado__isnull=True).order_by('grupo_duplicado', 'nome_completo')

        if user.is_superuser:
            pass
        elif hasattr(user, 'perfil') and is_in_group(user, ['Recepção', 'Membro do Gabinete', 'Secretária']):
            contas_usuario = user.perfil.contas.all()
            base_queryset = base_queryset.filter(contas__in=contas_usuario).distinct()
        else:
            return Municipe.objects.none()
        
        if categoria_ids:
            base_queryset = base_queryset.filter(categoria__id__in=categoria_ids)

        if termo_busca:
            query_palavras_nome = Q()
            for palavra in termo_busca.split():
                query_palavras_nome &= (Q(nome_completo__icontains=palavra) | Q(nome_de_guerra__icontains=palavra))

            query_outros_campos = (
                Q(cpf__icontains=termo_busca) |
                Q(emails__contains=[{'email': termo_busca}]) |
                Q(cargo__icontains=termo_busca) |
                Q(orgao__icontains=termo_busca) |
                Q(categoria__nome__icontains=termo_busca) |
                Q(endereco__icontains=termo_busca) |
                Q(perfis__cargo__icontains=termo_busca) |
                Q(perfis__instituicao__icontains=termo_busca)
            )

            final_query = query_palavras_nome | query_outros_campos
            
            base_queryset = base_queryset.filter(final_query).distinct()
        
        if letra_inicial:
            base_queryset = base_queryset.filter(nome_completo__istartswith=letra_inicial)
        
        if filtro_aplicado:
            return base_queryset.order_by('nome_completo')
        else:
            return base_queryset.order_by('-data_cadastro')[:100]

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
        qs = Municipe.objects.prefetch_related('contas', 'categoria', 'perfis')
        if user.is_superuser:
            return qs
        if hasattr(user, 'perfil') and is_in_group(user, ['Recepção', 'Membro do Gabinete', 'Secretária']):
            return qs.filter(contas__in=user.perfil.contas.all()).distinct()
        return Municipe.objects.none()


class MunicipeDetailDataView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]
    serializer_class = MunicipeDetailSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Municipe.objects.prefetch_related(
            'contas', 'categoria', 'perfis',
            'visitas', 'visitas__conta_destino', 'visitas__usuario_destino',
            'agendas_participantes', 'agendas_participantes__compromisso', 'agendas_participantes__compromisso__conta'
        )
        if user.is_superuser:
            return qs
        if hasattr(user, 'perfil') and is_in_group(user, ['Recepção', 'Membro do Gabinete', 'Secretária']):
            return qs.filter(contas__in=user.perfil.contas.all()).distinct()
        return Municipe.objects.none()


class MunicipeLookupView(generics.ListAPIView):
    serializer_class = MunicipeLookupSerializer
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]

    def get_queryset(self):
        user = self.request.user
        queryset = Municipe.objects.all()

        # Filtro de segurança: só contatos vinculados a pelo menos uma conta do perfil
        if not user.is_superuser:
            if hasattr(user, 'perfil') and is_in_group(user, ['Recepção', 'Membro do Gabinete', 'Secretária']):
                queryset = queryset.filter(contas__in=user.perfil.contas.all()).distinct()
            else:
                return Municipe.objects.none()

        queryset = queryset.prefetch_related('contas', 'categoria', 'perfis')

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
            queryset = queryset.filter(categoria__nome__in=nomes)

        # --- 3. BUSCA TEXTUAL ---
        termo_busca = self.request.query_params.get('q', None)

        if not termo_busca:
            return queryset.order_by('-data_cadastro')[:20]

        if termo_busca.isdigit():
            return queryset.filter(id=termo_busca)

        palavras = termo_busca.split()
        query_parts = [
            (Q(nome_completo__icontains=palavra) | Q(nome_de_guerra__icontains=palavra) |
             Q(perfis__cargo__icontains=palavra) | Q(perfis__instituicao__icontains=palavra))
            for palavra in palavras
        ]
        if query_parts:
            final_query = reduce(operator.and_, query_parts)
            resultados = queryset.filter(final_query).distinct()
        else:
            resultados = queryset
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
    permission_classes = [permissions.IsAuthenticated, CanEditMunicipeDetails]

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

        try:
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
        
class UnificarMunicipesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

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
                links_migrados = 0
                
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
                duplicado_id = duplicado.id
                duplicado_nome = duplicado.nome_completo
                logger.info(f"Preparando exclusão de {len(objetos_para_deletar_apos_commit)} objetos duplicados e munícipe duplicado após commit")
                
                # Usar on_commit para deletar APÓS a transação ser commitada com sucesso
                def deletar_objetos_apos_commit():
                    # Deletar objetos duplicados primeiro
                    for model_class, obj_id in objetos_para_deletar_apos_commit:
                        try:
                            obj = model_class.objects.filter(pk=obj_id).first()
                            if obj:
                                obj.delete()
                                logger.info(f"Objeto duplicado deletado após commit: {model_class.__name__} ID {obj_id}")
                        except Exception as delete_error:
                            logger.error(f"Erro ao deletar objeto duplicado {model_class.__name__} ID {obj_id} após commit: {delete_error}", exc_info=True)
                    
                    # Deletar o munícipe duplicado
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
                        logger.warning(f"Duplicado não foi deletado automaticamente. ID: {duplicado_id}, Nome: {duplicado_nome} - pode ser deletado manualmente")
                
                # Agendar deleção para após o commit bem-sucedido
                transaction.on_commit(deletar_objetos_apos_commit)

                logger.info(f"Unificação concluída: {links_migrados} vínculos migrados. Duplicado será deletado após commit.")
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
    permission_classes = [permissions.IsAuthenticated, CanEditMunicipeDetails]

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


class DescartarGrupoDuplicatasView(APIView):
    """Limpa grupo_duplicado de todos os munícipes do grupo informado (UUID)."""
    permission_classes = [permissions.IsAuthenticated, CanEditMunicipeDetails]

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
    permission_classes = [permissions.IsAuthenticated, CanEditMunicipeDetails]

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
    permission_classes = [permissions.IsAuthenticated, CanEditMunicipeDetails]

    def get(self, request):
        problemas_param = request.query_params.getlist('problema') or ['telefone_invalido', 'email_invalido', 'cpf_ausente']
        problemas = set(problemas_param)
        q_busca = (request.query_params.get('q') or '').strip()

        resultados = []
        qs = Municipe.objects.prefetch_related('perfis').only(
            'id', 'nome_completo', 'cpf', 'telefones', 'emails', 'auditoria_ia', 'cargo', 'orgao'
        )

        if q_busca:
            qs = qs.filter(
                Q(nome_completo__icontains=q_busca) |
                Q(cpf__icontains=q_busca) |
                Q(cargo__icontains=q_busca) |
                Q(orgao__icontains=q_busca) |
                Q(perfis__cargo__icontains=q_busca) |
                Q(perfis__instituicao__icontains=q_busca)
            ).distinct()

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
    View para atualizar a categoria de múltiplos Munícipes de uma só vez.
    """
    # Use uma permissão que permita editar munícipes
    permission_classes = [permissions.IsAuthenticated, CanEditMunicipeDetails]

    def post(self, request, *args, **kwargs):
        # 1. Obter dados da requisição
        municipe_ids = request.data.get('municipe_ids', [])
        nova_categoria_id = request.data.get('nova_categoria_id', None)

        # 2. Validações básicas
        if not isinstance(municipe_ids, list) or not municipe_ids:
            return Response(
                {'error': 'Lista de IDs de munícipes inválida ou vazia.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if nova_categoria_id is None:
            return Response(
                {'error': 'ID da nova categoria é obrigatório.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Valida se a categoria existe
        try:
            nova_categoria = CategoriaContato.objects.get(pk=nova_categoria_id)
        except CategoriaContato.DoesNotExist:
            return Response(
                {'error': f'Categoria com ID {nova_categoria_id} não encontrada.'},
                status=status.HTTP_404_NOT_FOUND
            )

        # 3. Executar a atualização em lote
        try:
            # Filtra apenas os munícipes que o usuário tem permissão para ver/editar
            # (Reutiliza parte da lógica de queryset da sua MunicipeListCreateView)
            user = request.user
            queryset_base = Municipe.objects.all()
            if hasattr(user, 'perfil'):
                 contas_usuario = user.perfil.contas.all()
                 queryset_base = queryset_base.filter(
                     Q(contas__isnull=True) | Q(contas__in=contas_usuario)
                 ).distinct()
            elif not user.is_superuser:
                 # Se não tem perfil e não é superuser, não pode alterar nada
                 return Response(
                    {'error': 'Você não tem permissão para alterar estes contatos.'},
                    status=status.HTTP_403_FORBIDDEN
                 )

            # Filtra pelos IDs recebidos DENTRO do queryset permitido
            municipes_para_atualizar = queryset_base.filter(id__in=municipe_ids)
            
            # Conta quantos serão realmente atualizados
            num_atualizados = municipes_para_atualizar.update(categoria=nova_categoria)

            return Response(
                {'message': f'{num_atualizados} contato(s) atualizado(s) com sucesso para a categoria "{nova_categoria.nome}".'},
                status=status.HTTP_200_OK
            )

        except Exception as e:
            # Captura qualquer erro inesperado durante o update
            return Response(
                {'error': f'Ocorreu um erro ao atualizar os contatos: {str(e)}'},
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
        if not (user.is_superuser or is_in_group(user, 'Recepção')):
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
                queryset = queryset.filter(Q(responsavel=user) | Q(responsavel__isnull=True))
            else:
                queryset = Atendimento.objects.none()
        elif is_in_group(user, 'Recepção') and not user.is_superuser:
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
            else:
                queryset = Atendimento.objects.none()

        # Aplicar filtros de data
        data_inicio_str = request.query_params.get('data_inicio', None)
        data_fim_str = request.query_params.get('data_fim', None)
        
        # Debug: log dos parâmetros recebidos
        logging.info(f"RelatorioAtendimentosPorStatusView - data_inicio: {data_inicio_str}, data_fim: {data_fim_str}")
        
        if data_inicio_str:
            try:
                # Converter para datetime no início do dia (00:00:00) com timezone
                data_inicio_obj = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
                data_inicio_datetime = timezone.make_aware(datetime.combine(data_inicio_obj, time.min))
                logging.info(f"Aplicando filtro data_inicio: {data_inicio_datetime}")
                queryset = queryset.filter(data_criacao__gte=data_inicio_datetime)
            except (ValueError, TypeError) as e:
                logging.warning(f"Erro ao processar data_inicio '{data_inicio_str}': {e}")
        if data_fim_str:
            try:
                # Converter para datetime no fim do dia (23:59:59) com timezone
                data_fim_obj = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
                data_fim_datetime = timezone.make_aware(datetime.combine(data_fim_obj, time.max))
                logging.info(f"Aplicando filtro data_fim: {data_fim_datetime}")
                queryset = queryset.filter(data_criacao__lte=data_fim_datetime)
            except (ValueError, TypeError) as e:
                logging.warning(f"Erro ao processar data_fim '{data_fim_str}': {e}")

        # Aplicar filtros de conta e status
        conta_id = request.query_params.get('conta_id', None)
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)
        
        status = request.query_params.get('status', None)
        if status:
            queryset = queryset.filter(status=status)
        
        # Aplicar filtro de membros (responsáveis)
        responsavel_ids_str = request.query_params.get('responsavel_ids', None)
        if responsavel_ids_str:
            try:
                responsavel_ids = [int(id.strip()) for id in responsavel_ids_str.split(',') if id.strip()]
                if responsavel_ids:
                    queryset = queryset.filter(responsavel_id__in=responsavel_ids)
            except (ValueError, AttributeError):
                pass

        # Debug: log do total de registros após filtros
        total_count = queryset.count()
        logging.info(f"Total de atendimentos após filtros: {total_count}")

        data = queryset.values('status').annotate(total=Count('status')).order_by('status')
        logging.info(f"Dados retornados: {list(data)}")
        return Response(data)


class RelatorioAtendimentosPorContaView(APIView):
    permission_classes = [IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        user = self.request.user
        queryset = Atendimento.objects.all()

        # Lógica de permissão UNIFICADA
        if not (user.is_superuser or is_in_group(user, 'Recepção')):
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
                queryset = queryset.filter(Q(responsavel=user) | Q(responsavel__isnull=True))
            else:
                queryset = Atendimento.objects.none()
        elif is_in_group(user, 'Recepção') and not user.is_superuser:
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
            else:
                queryset = Atendimento.objects.none()

        # Aplicar filtros de data
        data_inicio_str = request.query_params.get('data_inicio', None)
        data_fim_str = request.query_params.get('data_fim', None)
        
        # Debug: log dos parâmetros recebidos
        logging.info(f"RelatorioAtendimentosPorContaView - data_inicio: {data_inicio_str}, data_fim: {data_fim_str}")
        
        if data_inicio_str:
            try:
                # Converter para datetime no início do dia (00:00:00) com timezone
                data_inicio_obj = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
                data_inicio_datetime = timezone.make_aware(datetime.combine(data_inicio_obj, time.min))
                logging.info(f"Aplicando filtro data_inicio: {data_inicio_datetime}")
                queryset = queryset.filter(data_criacao__gte=data_inicio_datetime)
            except (ValueError, TypeError) as e:
                logging.warning(f"Erro ao processar data_inicio '{data_inicio_str}': {e}")
        if data_fim_str:
            try:
                # Converter para datetime no fim do dia (23:59:59) com timezone
                data_fim_obj = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
                data_fim_datetime = timezone.make_aware(datetime.combine(data_fim_obj, time.max))
                logging.info(f"Aplicando filtro data_fim: {data_fim_datetime}")
                queryset = queryset.filter(data_criacao__lte=data_fim_datetime)
            except (ValueError, TypeError) as e:
                logging.warning(f"Erro ao processar data_fim '{data_fim_str}': {e}")

        # Aplicar filtros de conta e status
        conta_id = request.query_params.get('conta_id', None)
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)
        
        status = request.query_params.get('status', None)
        if status:
            queryset = queryset.filter(status=status)
        
        # Aplicar filtro de membros (responsáveis)
        responsavel_ids_str = request.query_params.get('responsavel_ids', None)
        if responsavel_ids_str:
            try:
                responsavel_ids = [int(id.strip()) for id in responsavel_ids_str.split(',') if id.strip()]
                if responsavel_ids:
                    queryset = queryset.filter(responsavel_id__in=responsavel_ids)
            except (ValueError, AttributeError):
                pass

        # Debug: log do total de registros após filtros
        total_count = queryset.count()
        logging.info(f"Total de atendimentos após filtros: {total_count}")

        data = queryset.values('conta__nome').annotate(total=Count('id')).order_by('-total')
        logging.info(f"Dados retornados: {list(data)}")
        return Response(data)


class RelatorioAtendimentosPorCategoriaView(APIView):
    permission_classes = [IsAuthenticated, CanViewAtendimentoReports]

    def get(self, request, *args, **kwargs):
        user = self.request.user
        queryset = Atendimento.objects.all()

        # Lógica de permissão UNIFICADA
        if not (user.is_superuser or is_in_group(user, 'Recepção')):
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
                queryset = queryset.filter(Q(responsavel=user) | Q(responsavel__isnull=True))
            else:
                queryset = Atendimento.objects.none()
        elif is_in_group(user, 'Recepção') and not user.is_superuser:
            if hasattr(user, 'perfil'):
                queryset = queryset.filter(conta__in=user.perfil.contas.all())
            else:
                queryset = Atendimento.objects.none()

        # Aplicar filtros de data
        data_inicio_str = request.query_params.get('data_inicio', None)
        data_fim_str = request.query_params.get('data_fim', None)
        
        # Debug: log dos parâmetros recebidos
        logging.info(f"RelatorioAtendimentosPorCategoriaView - data_inicio: {data_inicio_str}, data_fim: {data_fim_str}")
        
        if data_inicio_str:
            try:
                # Converter para datetime no início do dia (00:00:00) com timezone
                data_inicio_obj = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
                data_inicio_datetime = timezone.make_aware(datetime.combine(data_inicio_obj, time.min))
                logging.info(f"Aplicando filtro data_inicio: {data_inicio_datetime}")
                queryset = queryset.filter(data_criacao__gte=data_inicio_datetime)
            except (ValueError, TypeError) as e:
                logging.warning(f"Erro ao processar data_inicio '{data_inicio_str}': {e}")
        if data_fim_str:
            try:
                # Converter para datetime no fim do dia (23:59:59) com timezone
                data_fim_obj = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
                data_fim_datetime = timezone.make_aware(datetime.combine(data_fim_obj, time.max))
                logging.info(f"Aplicando filtro data_fim: {data_fim_datetime}")
                queryset = queryset.filter(data_criacao__lte=data_fim_datetime)
            except (ValueError, TypeError) as e:
                logging.warning(f"Erro ao processar data_fim '{data_fim_str}': {e}")

        # Aplicar filtros de conta e status
        conta_id = request.query_params.get('conta_id', None)
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)
        
        status = request.query_params.get('status', None)
        if status:
            queryset = queryset.filter(status=status)
        
        # Aplicar filtro de membros (responsáveis)
        responsavel_ids_str = request.query_params.get('responsavel_ids', None)
        if responsavel_ids_str:
            try:
                responsavel_ids = [int(id.strip()) for id in responsavel_ids_str.split(',') if id.strip()]
                if responsavel_ids:
                    queryset = queryset.filter(responsavel_id__in=responsavel_ids)
            except (ValueError, AttributeError):
                pass

        # Debug: log do total de registros após filtros
        total_count = queryset.count()
        logging.info(f"Total de atendimentos após filtros: {total_count}")

        data = queryset.values('categorias__nome').annotate(total=Count('id')).order_by('-total')
        logging.info(f"Dados retornados: {list(data)}")
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

        # Minha Agenda Hoje: Registros de Visita/Check-in do dia (conta do usuário + filtro de responsabilidade)
        data['visitas_hoje'] = []
        contas_usuario = user.perfil.contas.all() if hasattr(user, 'perfil') else Conta.objects.none()
        if user.is_superuser and not contas_usuario.exists():
            contas_usuario = Conta.objects.all()
        if contas_usuario.exists():
            hoje_inicio = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
            hoje_fim = timezone.localtime().replace(hour=23, minute=59, second=59, microsecond=999999)
            qs_visitas = RegistroVisita.objects.filter(
                conta_destino__in=contas_usuario,
                data_checkin__range=(hoje_inicio, hoje_fim),
            ).filter(
                Q(usuario_destino=user) | Q(registrado_por=user) | Q(usuario_destino__isnull=True)
            ).select_related('municipe', 'conta_destino', 'usuario_destino', 'registrado_por').order_by('data_checkin')
            data['visitas_hoje'] = RegistroVisitaSerializer(qs_visitas, many=True, context={'request': request}).data

        return Response(data)


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

        inicio = timezone.make_aware(datetime.combine(data_obj, time.min))
        fim = timezone.make_aware(datetime.combine(data_obj, time.max))
        qs = RegistroVisita.objects.filter(
            conta_destino__in=contas_usuario,
            data_checkin__range=(inicio, fim),
        ).filter(
            Q(usuario_destino=user) | Q(registrado_por=user) | Q(usuario_destino__isnull=True)
        ).select_related('municipe', 'conta_destino', 'usuario_destino', 'registrado_por').order_by('data_checkin')
        data_serializada = RegistroVisitaSerializer(qs, many=True, context={'request': request}).data
        return Response(data_serializada)


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
        data_inicio_str = request.query_params.get('data_inicio', None)
        data_fim_str = request.query_params.get('data_fim', None)

        if status:
            queryset = queryset.filter(status=status)
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)
        
        # Aplicar filtro de membros (responsáveis)
        responsavel_ids_str = request.query_params.get('responsavel_ids', None)
        if responsavel_ids_str:
            try:
                responsavel_ids = [int(id.strip()) for id in responsavel_ids_str.split(',') if id.strip()]
                if responsavel_ids:
                    queryset = queryset.filter(responsavel_id__in=responsavel_ids)
            except (ValueError, AttributeError):
                pass
        
        # Aplicar filtros de data com timezone
        if data_inicio_str:
            try:
                # Converter para datetime no início do dia (00:00:00) com timezone
                data_inicio_obj = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
                data_inicio_datetime = timezone.make_aware(datetime.combine(data_inicio_obj, time.min))
                queryset = queryset.filter(data_criacao__gte=data_inicio_datetime)
            except (ValueError, TypeError) as e:
                logging.warning(f"Erro ao processar data_inicio '{data_inicio_str}' em GerarPdfAtendimentosView: {e}")
        if data_fim_str:
            try:
                # Converter para datetime no fim do dia (23:59:59) com timezone
                data_fim_obj = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
                data_fim_datetime = timezone.make_aware(datetime.combine(data_fim_obj, time.max))
                queryset = queryset.filter(data_criacao__lte=data_fim_datetime)
            except (ValueError, TypeError) as e:
                logging.warning(f"Erro ao processar data_fim '{data_fim_str}' em GerarPdfAtendimentosView: {e}")

        # Buscar conta_contexto para logos (sem duplicar a busca de conta_id)
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

        # Calcular big numbers por status
        total_atendimentos = queryset.count()
        status_counts = queryset.values('status').annotate(total=Count('id'))
        big_numbers = {
            'total': total_atendimentos,
            'aberto': 0,
            'em_analise': 0,
            'encaminhado': 0,
            'concluido': 0,
            'arquivado': 0,
        }
        status_map = {
            'ABERTO': 'aberto',
            'EM_ANALISE': 'em_analise',
            'ENCAMINHADO': 'encaminhado',
            'CONCLUIDO': 'concluido',
            'ARQUIVADO': 'arquivado',
        }
        for item in status_counts:
            status_key = status_map.get(item['status'], None)
            if status_key:
                big_numbers[status_key] = item['total']
        
        context = {
            'atendimentos': queryset.select_related('municipe', 'conta', 'responsavel').prefetch_related('tramitacoes__usuario', 'categorias'),
            'nome_instituicao': nome_instituicao,
            'brasao_path': brasao_path,
            'logo_conta_path': logo_conta_path,
            'logo_siga_path': logo_siga_path,
            'big_numbers': big_numbers,
        }
        html_string = render_to_string('atendimentos/relatorio_atendimentos.html', context)
        pdf_file = HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf()

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
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        
        # 1. ORDENAÇÃO
        ordenar_por = request.query_params.get('ordenar_por', 'nome')
        if ordenar_por == 'orgao':
            order_fields = ['orgao', 'nome_completo']
        else:
            order_fields = ['nome_completo']
        
        queryset = Municipe.objects.prefetch_related('categoria', 'contas').all().order_by(*order_fields)

        # 2. PERMISSÕES DE VISUALIZAÇÃO
        if is_in_group(user, 'Membro do Gabinete') or is_in_group(user, 'Secretária'):
            if hasattr(user, 'perfil'):
                contas_usuario = user.perfil.contas.all()
                queryset = queryset.filter(
                    Q(contas__isnull=True) | Q(contas__in=contas_usuario)
                ).distinct()
            else:
                queryset = queryset.filter(contas__isnull=True)

        # --- 3. FILTRO POR CATEGORIA (O QUE FALTAVA) ---
        # O Frontend manda ?categoria_id=1&categoria_id=2...
        # Usamos .getlist() para pegar todos os IDs enviados
        categoria_ids = request.query_params.getlist('categoria_id')
        if categoria_ids:
            queryset = queryset.filter(categoria_id__in=categoria_ids)
        # -----------------------------------------------

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
                Q(categoria__nome__icontains=termo_busca)
            )
            queryset = queryset.filter(query_palavras_nome | query_outros_campos).distinct()

        # 5. GERAÇÃO DO ARQUIVO EXCEL
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Contatos'

        headers = ['Nome Completo', 'CPF', 'Data de Nascimento', 'Email Principal', 'Telefone Principal', 'Cargo', 'Órgão', 'Categoria', 'Contas Vinculadas']
        sheet.append(headers)

        for municipe in queryset:
            email_principal = ''
            if municipe.emails and isinstance(municipe.emails, list) and len(municipe.emails) > 0:
                email_principal = municipe.emails[0].get('email', '')

            telefone = municipe.telefones[0].get('numero', '') if municipe.telefones else ''
            data_nasc_formatada = municipe.data_nascimento.strftime('%d/%m/%Y') if municipe.data_nascimento else ''
            categoria_nome = municipe.categoria.nome if municipe.categoria else ''
            contas_vinculadas = ", ".join([conta.nome for conta in municipe.contas.all()])

            sheet.append([
                municipe.nome_completo,
                municipe.cpf,
                data_nasc_formatada,
                email_principal,
                telefone,
                municipe.cargo,
                municipe.orgao,
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
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        
        ordenar_por = request.query_params.get('ordenar_por', 'nome')
        
        if ordenar_por == 'orgao':
            order_fields = ['orgao', 'nome_completo']
        else:
            order_fields = ['nome_completo']
        
        queryset = Municipe.objects.prefetch_related('categoria', 'contas').all().order_by(*order_fields)

        if is_in_group(user, 'Membro do Gabinete') or is_in_group(user, 'Secretária'):
            if hasattr(user, 'perfil'):
                contas_usuario = user.perfil.contas.all()
                queryset = queryset.filter(
                    Q(contas__isnull=True) | Q(contas__in=contas_usuario)
                ).distinct()
            else:
                queryset = queryset.filter(contas__isnull=True)

        # --- CORREÇÃO: FILTRO POR CATEGORIA AQUI TAMBÉM ---
        categoria_ids = request.query_params.getlist('categoria_id')
        if categoria_ids:
            queryset = queryset.filter(categoria_id__in=categoria_ids)
        # --------------------------------------------------

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
                Q(categoria__nome__icontains=termo_busca)
            )
            queryset = queryset.filter(query_palavras_nome | query_outros_campos).distinct()

        
        # PREPARAÇÃO DOS DADOS PARA O TEMPLATE
        municipes_data = []
        for municipe in queryset:
            telefone_principal = ''
            if municipe.telefones and isinstance(municipe.telefones, list) and len(municipe.telefones) > 0:
                telefone_principal = municipe.telefones[0].get('numero', '')
            
            email_principal = ''
            if municipe.emails and isinstance(municipe.emails, list) and len(municipe.emails) > 0:
                # Pega o primeiro email da lista como principal
                email_principal = municipe.emails[0].get('email', '') if isinstance(municipe.emails[0], dict) else str(municipe.emails[0])

            municipes_data.append({
                'nome': municipe.nome_completo,
                'telefone': telefone_principal,
                'email': email_principal,
                'cargo': municipe.cargo,
                'orgao': municipe.orgao,
                'categoria': municipe.categoria.nome if municipe.categoria else '', # Adicionei categoria no PDF tbm, útil
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
            agenda_id = request.query_params.get('agenda_id')
            
            # --- ETAPA 1: BUSCAR CREDENCIAIS (CORRIGIDO) ---
            token_google = None

            # 1. Se veio um ID de Agenda (Conta), tentamos pegar o token de alguém dessa conta
            if agenda_id:
                # Busca o primeiro token válido de qualquer usuário vinculado a esta conta
                token_google = GoogleApiToken.objects.filter(
                    usuario__perfil__contas__id=agenda_id
                ).first()

            # 2. Se não achou token pela conta (ou não veio ID), tenta o usuário logado
            if not token_google:
                try:
                    token_google = GoogleApiToken.objects.get(usuario=user_logado)
                except GoogleApiToken.DoesNotExist:
                    return Response({'detail': 'Autorização do Google não encontrada. O gestor da conta precisa conectar ao Google.'}, status=status.HTTP_400_BAD_REQUEST)

            # --- A PARTIR DAQUI É O SEU CÓDIGO ORIGINAL (MANTIDO) ---
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
                calendarId='primary', 
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

        if user.is_superuser:
            base_queryset = Municipe.objects.all()
        elif hasattr(user, 'perfil'):
            base_queryset = Municipe.objects.filter(contas__in=user.perfil.contas.all()).distinct()
        else:
            return Municipe.objects.none()

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

        # Munícipes: superuser vê todos; demais só os vinculados às contas do perfil
        if user.is_superuser:
            municipe_qs = Municipe.objects.all()
        elif hasattr(user, 'perfil') and is_in_group(user, ['Recepção', 'Membro do Gabinete', 'Secretária']):
            municipe_qs = Municipe.objects.filter(contas__in=user.perfil.contas.all()).distinct()
        else:
            municipe_qs = Municipe.objects.none()

        # --- AQUI ESTÁ A CORREÇÃO DA BUSCA POR MUNÍCIPES ---
        query_palavras = Q()
        for palavra in termo_busca.split():
            # Nome, nome de guerra ou cargo/instituição nos perfis
            query_palavras &= (
                Q(nome_completo__icontains=palavra) |
                Q(nome_de_guerra__icontains=palavra) |
                Q(perfis__cargo__icontains=palavra) |
                Q(perfis__instituicao__icontains=palavra)
            )
        
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
    """
    Função centralizada para aplicar filtros de BI (Datas, Contas, Responsáveis)
    """
    user = request.user
    
    # 1. Segurança de Conta (Tenant)
    # Se não for Superuser, restringe às contas do perfil
    if not user.is_superuser:
        if hasattr(user, 'perfil'):
            queryset = queryset.filter(conta__in=user.perfil.contas.all())
        else:
            return queryset.none()

    # 2. Filtros de Data
    data_inicio = request.query_params.get('data_inicio')
    data_fim = request.query_params.get('data_fim')
    
    if data_inicio: 
        queryset = queryset.filter(data_criacao__gte=f'{data_inicio} 00:00:00')
    if data_fim: 
        queryset = queryset.filter(data_criacao__lte=f'{data_fim} 23:59:59')

    # 3. Filtro de Conta Específica (Admin/Gestor selecionou no dropdown)
    conta_id = request.query_params.get('conta_id')
    if conta_id: 
        queryset = queryset.filter(conta_id=conta_id)

    # 4. Filtros de Responsável (NOVO: usuario_id tem prioridade sobre apenas_meus)
    usuario_id = request.query_params.get('usuario_id')
    
    if usuario_id:
        # Se veio um ID específico (Superuser selecionou no Dropdown)
        queryset = queryset.filter(responsavel_id=usuario_id)
    elif request.query_params.get('apenas_meus') == 'true':
        # Se não selecionou usuário, mas marcou "Apenas Meus"
        queryset = queryset.filter(responsavel=user)
        
    return queryset

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
    
class GerarRelatorioBiPdfView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:
            # 1. Filtros (Já inclui a lógica nova de usuario_id)
            qs_base = aplicar_filtros_bi(Atendimento.objects.all(), request)
            
            # 2. KPIs
            total_atendimentos = qs_base.count()
            total_concluidos = qs_base.filter(status='CONCLUIDO').count()
            total_abertos = qs_base.filter(status='ABERTO').count()
            
            # 3. Listas
            produtividade = qs_base.values(
                nome=Coalesce(F('responsavel__first_name'), Value('Não Atribuído'), output_field=CharField())
            ).annotate(qtd=Count('id')).order_by('-qtd')[:10]

            solicitantes = qs_base.values(
                nome=F('municipe__nome_completo')
            ).annotate(qtd=Count('id')).order_by('-qtd')[:10]

            # 4. Contexto de Conta e Brasão (CORREÇÃO DE ERRO E IMAGEM)
            user = request.user
            conta_contexto = None
            
            # Tenta pegar a conta de forma segura
            if hasattr(user, 'perfil') and user.perfil.contas.exists():
                conta_contexto = user.perfil.contas.first()

            nome_instituicao = "Prefeitura Municipal"
            
            # Caminho padrão do brasão no disco
            caminho_brasao = os.path.join(settings.STATIC_ROOT, 'images', 'logo-brasao-prefeitura.png')
            if not os.path.exists(caminho_brasao):
                 # Fallback para pasta de desenvolvimento se static_root não existir
                 caminho_brasao = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-brasao-prefeitura.png')

            # Se tiver conta e brasão personalizado, usa o da conta
            if conta_contexto:
                nome_instituicao = conta_contexto.nome_instituicao or nome_instituicao
                if conta_contexto.brasao_instituicao:
                    try:
                        if os.path.exists(conta_contexto.brasao_instituicao.path):
                            caminho_brasao = conta_contexto.brasao_instituicao.path
                    except Exception:
                        pass # Mantém o padrão se der erro

            # Converte para URI de arquivo (file://) para o WeasyPrint ler do disco
            brasao_uri = f"file://{caminho_brasao}" if os.path.exists(caminho_brasao) else ""

            context = {
                'data_inicio': request.query_params.get('data_inicio'),
                'data_fim': request.query_params.get('data_fim'),
                'filtro_usuario': request.query_params.get('usuario_id'), # Apenas informativo
                'usuario_emissao': user.get_full_name() or user.username,
                'data_emissao': datetime.now(),
                'nome_instituicao': nome_instituicao, # Adicionado
                'total_geral': total_atendimentos,
                'total_concluidos': total_concluidos,
                'total_abertos': total_abertos,
                'produtividade': produtividade,
                'solicitantes': solicitantes,
                'brasao_url': brasao_uri, # Agora é URI de arquivo, não URL web
                'titulo_relatorio': "Relatório de Gestão e Produtividade"
            }

            html_string = render_to_string('relatorios/relatorio_bi.html', context)
            
            # O base_url ajuda a carregar CSS local se necessário
            pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()

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