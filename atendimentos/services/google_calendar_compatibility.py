"""
Serviço de compatibilidade para múltiplas contas Google Calendar.

Este módulo mantém a API existente funcionando enquanto adiciona
suporte para múltiplas contas Google por gabinete.

Uso:
    # Código existente continua funcionando:
    service = GoogleCalendarCompatibilityService.get_calendar_service_legado(user)
    
    # Nova API para conta específica:
    service = GoogleCalendarCompatibilityService.get_calendar_service(user, conta_google_id)
"""
import logging
from typing import Optional, List
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleAuthRequest

from ..models import (
    Conta, 
    ContaGoogleCalendar, 
    TokenGoogleCalendar, 
    UsuarioContaGooglePermissao,
    GoogleApiToken
)

logger = logging.getLogger(__name__)


class GoogleCalendarPermissionError(Exception):
    """Exceção para erros de permissão de acesso a contas Google"""
    pass


class GoogleCalendarAuthError(Exception):
    """Exceção para erros de autenticação OAuth"""
    pass


class GoogleCalendarCompatibilityService:
    """
    Serviço de compatibilidade que mantém código existente funcionando
    enquanto adiciona suporte a múltiplas contas Google.
    """
    
    # ========================================
    # MÉTODOS DE COMPATIBILIDADE (API ANTIGA)
    # ========================================
    
    @staticmethod
    def get_calendar_service_legado(user: User):
        """
        Método de compatibilidade que mantém a API antiga funcionando.
        
        Retorna serviço da conta Google padrão do usuário.
        Se não houver conta migrada, tenta usar token legado.
        
        Args:
            user (User): Usuário logado
            
        Returns:
            googleapiclient.discovery.Resource: Serviço do Google Calendar
            
        Raises:
            GoogleCalendarAuthError: Se não há autenticação
            GoogleCalendarPermissionError: Se não tem permissão
        """
        # Tenta primeiro o novo sistema
        try:
            conta_google_padrao = GoogleCalendarCompatibilityService._get_conta_google_padrao_usuario(user)
            if conta_google_padrao:
                return GoogleCalendarCompatibilityService.get_calendar_service(
                    user, 
                    conta_google_padrao.id
                )
        except (GoogleCalendarAuthError, GoogleCalendarPermissionError):
            pass
        
        # Fallback para sistema legado
        logger.warning(f"Usando sistema legado para usuário {user.username}")
        return GoogleCalendarCompatibilityService._get_calendar_service_legado_fallback(user)
    
    @staticmethod
    def _get_calendar_service_legado_fallback(user: User):
        """Fallback para sistema legado (GoogleApiToken)"""
        try:
            # Busca token legado
            token_legado = GoogleApiToken.objects.get(usuario=user)
            
            # Verifica expiração
            if timezone.now() >= token_legado.expires_at:
                logger.warning(f"Token legado expirado para {user.username}")
                raise GoogleCalendarAuthError("Token legado expirado")
            
            # Cria credentials
            credentials = Credentials(
                token=token_legado.access_token,
                refresh_token=token_legado.refresh_token,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=settings.GOOGLE_CLIENT_ID,
                client_secret=settings.GOOGLE_CLIENT_SECRET
            )
            
            return build('calendar', 'v3', credentials=credentials)
            
        except GoogleApiToken.DoesNotExist:
            raise GoogleCalendarAuthError("Usuário precisa fazer autenticação OAuth")
    
    # ========================================
    # NOVA API (MÚLTIPLAS CONTAS)
    # ========================================
    
    @staticmethod
    def get_calendar_service(
        user: User,
        conta_google_id: int,
        *,
        requer_token_proprio: bool = False,
    ):
        """
        Nova API - Retorna serviço para conta Google específica.

        requer_token_proprio: True para criar/editar (OAuth do usuário).
        False para listar (permite token delegado — leitura via SIGA).
        """
        # Busca conta Google
        try:
            conta_google = ContaGoogleCalendar.objects.get(
                id=conta_google_id,
                ativa=True
            )
        except ContaGoogleCalendar.DoesNotExist:
            raise GoogleCalendarPermissionError(f"Conta Google {conta_google_id} não encontrada ou inativa")
        
        # Verifica permissão
        logger.info(f"🔍 Verificando permissão para usuário {user.username} (ID: {user.id}) na conta {conta_google.nome}")
        GoogleCalendarCompatibilityService._verificar_permissao_usuario(user, conta_google)
        logger.info(f"✅ Permissão OK para usuário {user.username}")
        
        # Busca token próprio ou delegado (leitura via SIGA)
        logger.info(f"🔍 Buscando token para usuário {user.username} na conta {conta_google.nome}")
        token = GoogleCalendarCompatibilityService._resolver_token_acesso(
            user, conta_google, requer_token_proprio=requer_token_proprio
        )
        logger.info(f"✅ Token obtido com sucesso: expira em {token.expires_at}")
        
        # Cria credentials
        credentials = Credentials(
            token=token.access_token,
            refresh_token=token.refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=conta_google.get_client_id(),
            client_secret=conta_google.get_client_secret()
        )
        
        return build('calendar', 'v3', credentials=credentials)
    
    @staticmethod
    def get_contas_google_usuario(user: User) -> List[ContaGoogleCalendar]:
        """
        Retorna todas as contas Google que o usuário tem permissão para visualizar.
        
        Args:
            user (User): Usuário logado
            
        Returns:
            List[ContaGoogleCalendar]: Lista de contas Google acessíveis
        """
        conta_usuario = GoogleCalendarCompatibilityService._get_conta_usuario(user)
        if not conta_usuario:
            return []
        
        return list(conta_usuario.get_contas_google_usuario(user))
    
    @staticmethod
    def usuario_pode_visualizar_eventos(user: User, conta_google_id: int) -> bool:
        """
        Verifica se usuário pode visualizar eventos em conta Google específica.
        
        Args:
            user (User): Usuário
            conta_google_id (int): ID da conta Google
            
        Returns:
            bool: True se pode visualizar, False caso contrário
        """
        try:
            permissao = UsuarioContaGooglePermissao.objects.get(
                usuario=user,
                conta_google_id=conta_google_id,
                conta_google__ativa=True
            )
            return permissao.pode_visualizar
        except UsuarioContaGooglePermissao.DoesNotExist:
            return False

    @staticmethod
    def usuario_pode_criar_evento(user: User, conta_google_id: int) -> bool:
        """
        Verifica se usuário pode criar eventos em conta Google específica.
        
        Args:
            user (User): Usuário
            conta_google_id (int): ID da conta Google
            
        Returns:
            bool: True se pode criar, False caso contrário
        """
        try:
            permissao = UsuarioContaGooglePermissao.objects.get(
                usuario=user,
                conta_google_id=conta_google_id,
                conta_google__ativa=True
            )
            return permissao.pode_criar
        except UsuarioContaGooglePermissao.DoesNotExist:
            return False
    
    # ========================================
    # MÉTODOS AUXILIARES PRIVADOS
    # ========================================
    
    @staticmethod
    def _get_conta_google_padrao_usuario(user: User) -> Optional[ContaGoogleCalendar]:
        """Retorna conta Google padrão do usuário"""
        conta_usuario = GoogleCalendarCompatibilityService._get_conta_usuario(user)
        if not conta_usuario:
            return None
        
        return conta_usuario.conta_google_padrao
    
    @staticmethod
    def _get_conta_usuario(user: User) -> Optional[Conta]:
        """
        Retorna a conta/gabinete associada ao usuário.
        
        🚨 IMPLEMENTAR baseado na lógica do seu sistema!
        """
        # Abordagem 1: Profile com conta
        if hasattr(user, 'profile') and hasattr(user.profile, 'conta'):
            return user.profile.conta
        
        # Abordagem 2: Grupos do Django
        grupos_usuario = user.groups.values_list('name', flat=True)
        for grupo_nome in grupos_usuario:
            conta = Conta.objects.filter(nome__iexact=grupo_nome).first()
            if conta:
                return conta
        
        # Abordagem 3: Fallback primeira conta com Google configurado
        return Conta.objects.exclude(
            google_calendar_id__isnull=True
        ).exclude(
            google_calendar_id__exact=''
        ).first()
    
    @staticmethod
    def _verificar_permissao_usuario(user: User, conta_google: ContaGoogleCalendar):
        """Verifica se usuário tem permissão para acessar conta Google"""
        try:
            permissao = UsuarioContaGooglePermissao.objects.get(
                usuario=user,
                conta_google=conta_google,
                pode_visualizar=True
            )
        except UsuarioContaGooglePermissao.DoesNotExist:
            raise GoogleCalendarPermissionError(
                f"Usuário {user.username} não tem acesso à conta Google: {conta_google.nome}"
            )
    
    @staticmethod
    def _get_or_refresh_token(user: User, conta_google: ContaGoogleCalendar) -> TokenGoogleCalendar:
        """Busca token do próprio usuário ou renova se expirado."""
        return GoogleCalendarCompatibilityService._resolver_token_acesso(
            user, conta_google, requer_token_proprio=True
        )

    @staticmethod
    def _resolver_token_acesso(
        user: User,
        conta_google: ContaGoogleCalendar,
        *,
        requer_token_proprio: bool = False,
    ) -> TokenGoogleCalendar:
        """
        Resolve token OAuth para acesso à agenda.

        - Token próprio: usuário autorizou o Google nesta conta.
        - Token delegado: outro usuário (ex.: Secretaria) já conectou; leitores
          visualizam eventos pelo SIGA sem OAuth próprio.
        """
        try:
            token = TokenGoogleCalendar.objects.get(
                usuario=user,
                conta_google=conta_google,
            )
            if token.is_expired:
                token = GoogleCalendarCompatibilityService._refresh_token(token, conta_google)
            return token
        except TokenGoogleCalendar.DoesNotExist:
            if requer_token_proprio:
                raise GoogleCalendarAuthError(
                    f"Usuário precisa fazer autenticação OAuth para: {conta_google.nome}"
                )

        token_delegado = GoogleCalendarCompatibilityService._buscar_token_delegado(conta_google)
        if token_delegado:
            logger.info(
                f"Token delegado de {token_delegado.usuario.username} "
                f"para leitura SIGA de {user.username} em {conta_google.nome}"
            )
            return token_delegado

        raise GoogleCalendarAuthError(
            f'Nenhum usuário conectou a agenda "{conta_google.nome}" no Google. '
            'Solicite à Secretaria ou ao administrador.'
        )

    @staticmethod
    def _buscar_token_delegado(conta_google: ContaGoogleCalendar) -> Optional[TokenGoogleCalendar]:
        """Primeiro token válido (ou renovável) vinculado à conta Google."""
        candidatos = (
            TokenGoogleCalendar.objects.filter(conta_google=conta_google)
            .select_related('usuario')
            .order_by('-data_atualizacao')
        )
        for token in candidatos:
            if not token.is_expired:
                return token
        for token in candidatos:
            if not token.refresh_token:
                continue
            try:
                return GoogleCalendarCompatibilityService._refresh_token(token, conta_google)
            except GoogleCalendarAuthError:
                continue
        return None

    @staticmethod
    def obter_status_token_usuario(user: User, conta_google: ContaGoogleCalendar) -> dict:
        """Status de conexão para API/frontend (inclui leitura delegada via SIGA)."""
        from ..serializers import build_token_status_payload

        permissao = None
        try:
            permissao = UsuarioContaGooglePermissao.objects.get(
                usuario=user,
                conta_google=conta_google,
            )
        except UsuarioContaGooglePermissao.DoesNotExist:
            pass

        token_proprio = None
        try:
            token_proprio = TokenGoogleCalendar.objects.get(
                usuario=user,
                conta_google=conta_google,
            )
        except TokenGoogleCalendar.DoesNotExist:
            pass

        token_delegado = None
        if not token_proprio or token_proprio.is_expired:
            token_delegado = GoogleCalendarCompatibilityService._buscar_token_delegado(conta_google)

        return build_token_status_payload(
            token_proprio=token_proprio,
            token_delegado=token_delegado,
            permissao=permissao,
        )
    
    @staticmethod
    def _refresh_token(token: TokenGoogleCalendar, conta_google: ContaGoogleCalendar) -> TokenGoogleCalendar:
        """Renova token expirado"""
        try:
            logger.info(f"Renovando token para usuário {token.usuario.username}")
            
            # Cria credentials para renovação
            credentials = Credentials(
                token=token.access_token,
                refresh_token=token.refresh_token,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=conta_google.get_client_id(),
                client_secret=conta_google.get_client_secret()
            )
            
            # Renova token
            request = GoogleAuthRequest()
            credentials.refresh(request)
            
            # Atualiza no banco
            token.access_token = credentials.token
            if credentials.refresh_token:
                token.refresh_token = credentials.refresh_token
            from ..models import _as_aware_datetime
            token.expires_at = _as_aware_datetime(credentials.expiry) or token.expires_at
            token.marcar_renovacao()
            token.save()
            
            logger.info(f"Token renovado com sucesso para {token.usuario.username}")
            return token
            
        except Exception as e:
            logger.error(f"Erro ao renovar token: {e}")
            raise GoogleCalendarAuthError(f"Erro ao renovar token: {e}")
    
    # ========================================
    # UTILITÁRIOS PARA DESENVOLVIMENTO
    # ========================================
    
    @staticmethod
    def get_auth_url(conta_google_id: int, redirect_uri: str) -> tuple:
        """
        Gera URL de autorização OAuth para conta Google específica.
        
        Args:
            conta_google_id (int): ID da conta Google
            redirect_uri (str): URI de callback
            
        Returns:
            tuple: (authorization_url, state)
        """
        conta_google = ContaGoogleCalendar.objects.get(id=conta_google_id)
        
        scopes = ['https://www.googleapis.com/auth/calendar.events']
        
        client_config = {
            "web": {
                "client_id": conta_google.get_client_id(),
                "client_secret": conta_google.get_client_secret(),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        
        flow = Flow.from_client_config(
            client_config, 
            scopes=scopes, 
            redirect_uri=redirect_uri
        )
        
        authorization_url, state = flow.authorization_url(
            access_type='offline', 
            prompt='consent'
        )
        
        return authorization_url, state
    
    @staticmethod
    def process_oauth_callback(conta_google_id: int, authorization_response: str, user: User) -> TokenGoogleCalendar:
        """
        Processa callback OAuth e salva tokens.
        
        Args:
            conta_google_id (int): ID da conta Google
            authorization_response (str): URL de resposta completa
            user (User): Usuário que está autenticando
            
        Returns:
            TokenGoogleCalendar: Token criado/atualizado
        """
        conta_google = ContaGoogleCalendar.objects.get(id=conta_google_id)
        
        # Verifica permissão
        GoogleCalendarCompatibilityService._verificar_permissao_usuario(user, conta_google)
        
        # Configura flow OAuth
        scopes = ['https://www.googleapis.com/auth/calendar.events']
        # Usar URL padrão se BASE_URL não estiver definida
        base_url = getattr(settings, 'BASE_URL', 'https://gabinete.mogidascruzes.sp.gov.br')
        redirect_uri = f"{base_url}/api/google-calendar/auth/{conta_google_id}/callback/"
        
        client_config = {
            "web": {
                "client_id": conta_google.get_client_id(),
                "client_secret": conta_google.get_client_secret(),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        
        flow = Flow.from_client_config(
            client_config,
            scopes=scopes,
            redirect_uri=redirect_uri
        )
        
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        
        from ..models import _as_aware_datetime

        # Salva ou atualiza token
        token, created = TokenGoogleCalendar.objects.update_or_create(
            usuario=user,
            conta_google=conta_google,
            defaults={
                'access_token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'expires_at': _as_aware_datetime(credentials.expiry),
            }
        )
        
        logger.info(
            f"Token {'criado' if created else 'atualizado'} para "
            f"{user.username} na conta {conta_google.nome}"
        )
        
        return token