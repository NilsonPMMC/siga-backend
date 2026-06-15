"""
Comando para migrar dados existentes do sistema Google Calendar legado 
para o novo sistema de múltiplas contas Google por gabinete.

Este comando deve ser executado APÓS aplicar as migrações Django.

Execução:
    python manage.py migrar_multiplas_contas_google

Opções:
    --dry-run: Executa simulação sem modificar dados
    --force: Força migração mesmo se já existirem dados migrados
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from atendimentos.models import (
    Conta, 
    GoogleApiToken, 
    ContaGoogleCalendar, 
    TokenGoogleCalendar, 
    UsuarioContaGooglePermissao
)


class Command(BaseCommand):
    help = 'Migra dados existentes para sistema de múltiplas contas Google'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Executa simulação sem modificar dados',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Força migração mesmo se já existirem dados migrados',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Mostra informações detalhadas',
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.force = options['force']
        self.verbose = options['verbose']
        
        if self.dry_run:
            self.stdout.write(
                self.style.WARNING('🧪 MODO SIMULAÇÃO - Nenhum dado será modificado')
            )
        
        self.stdout.write("🔄 Iniciando migração das contas Google...")
        
        try:
            with transaction.atomic():
                self._verificar_pre_requisitos()
                self._migrar_contas_google()
                self._migrar_tokens_usuarios()
                self._criar_relatorio_final()
                
                if self.dry_run:
                    # Força rollback no modo simulação
                    raise CommandError("Simulação concluída - dados não foram modificados")
                    
        except CommandError as e:
            if not self.dry_run:
                self.stdout.write(
                    self.style.ERROR(f'❌ Erro durante migração: {e}')
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS('✅ Simulação concluída com sucesso!')
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Erro inesperado: {e}')
            )
            raise

    def _verificar_pre_requisitos(self):
        """Verifica se o sistema está pronto para migração"""
        self.stdout.write("🔍 Verificando pré-requisitos...")
        
        # Verifica se há dados já migrados
        if ContaGoogleCalendar.objects.exists() and not self.force:
            raise CommandError(
                "Já existem contas Google migradas. Use --force para sobrescrever."
            )
        
        # Verifica configuração do .env
        if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
            raise CommandError(
                "GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET devem estar configurados no .env"
            )
        
        self.stdout.write("✅ Pré-requisitos verificados")

    def _migrar_contas_google(self):
        """Migra contas com google_calendar_id para ContaGoogleCalendar"""
        self.stdout.write("📊 Migrando contas Google...")
        
        contas_com_google = Conta.objects.exclude(
            google_calendar_id__isnull=True
        ).exclude(
            google_calendar_id__exact=''
        )
        
        self.contas_migradas = 0
        
        for conta in contas_com_google:
            self._migrar_conta_individual(conta)
            
        self.stdout.write(f"✅ {self.contas_migradas} contas Google migradas")

    def _migrar_conta_individual(self, conta):
        """Migra uma conta individual"""
        if self.verbose:
            self.stdout.write(f"  📝 Migrando: {conta.nome}")
        
        # Verifica se já existe conta migrada
        conta_google_existente = ContaGoogleCalendar.objects.filter(
            conta=conta,
            email_google=conta.google_calendar_id
        ).first()
        
        if conta_google_existente:
            if self.verbose:
                self.stdout.write(f"    ⚠️  Conta já migrada: {conta_google_existente}")
            return conta_google_existente
        
        # Cria nova conta Google
        conta_google_data = {
            'conta': conta,
            'nome': 'Agenda Principal',
            'descricao': 'Agenda principal migrada do sistema anterior',
            'email_google': conta.google_calendar_id,
            'calendar_id': conta.google_calendar_id,
            'usar_credenciais_globais': True,
            'client_id': '',  # Vazio = usa global
            'client_secret': '',  # Vazio = usa global
            'ativa': True,
            'eh_padrao': True
        }
        
        if not self.dry_run:
            conta_google = ContaGoogleCalendar.objects.create(**conta_google_data)
        else:
            # Simula criação
            conta_google = ContaGoogleCalendar(**conta_google_data)
            conta_google.id = 999  # ID fictício para simulação
        
        self.contas_migradas += 1
        
        if self.verbose:
            self.stdout.write(f"    ✅ Criada: {conta_google}")
        
        return conta_google

    def _migrar_tokens_usuarios(self):
        """Migra tokens e cria permissões para usuários"""
        self.stdout.write("🔑 Migrando tokens de usuários...")
        
        tokens_legado = GoogleApiToken.objects.filter(migrado=False)
        self.tokens_migrados = 0
        self.permissoes_criadas = 0
        
        for token in tokens_legado:
            self._migrar_token_usuario(token)
            
        self.stdout.write(f"✅ {self.tokens_migrados} tokens migrados")
        self.stdout.write(f"✅ {self.permissoes_criadas} permissões criadas")

    def _migrar_token_usuario(self, token_legado):
        """Migra token individual de usuário"""
        usuario = token_legado.usuario
        
        if self.verbose:
            self.stdout.write(f"  👤 Migrando token: {usuario.username}")
        
        # Busca conta do usuário (implementar lógica baseada no seu sistema)
        conta_usuario = self._get_conta_usuario(usuario)
        
        if not conta_usuario:
            if self.verbose:
                self.stdout.write(f"    ⚠️  Usuário sem conta associada: {usuario.username}")
            return
        
        # Busca conta Google padrão desta conta
        conta_google = ContaGoogleCalendar.objects.filter(
            conta=conta_usuario,
            ativa=True,
            eh_padrao=True
        ).first()
        
        if not conta_google:
            if self.verbose:
                self.stdout.write(f"    ⚠️  Conta Google não encontrada para: {conta_usuario.nome}")
            return
        
        # Migra token
        if not self.dry_run:
            token_novo, token_created = TokenGoogleCalendar.objects.get_or_create(
                usuario=usuario,
                conta_google=conta_google,
                defaults={
                    'access_token': token_legado.access_token,
                    'refresh_token': token_legado.refresh_token,
                    'expires_at': token_legado.expires_at,
                }
            )
        else:
            token_created = True  # Simula criação
        
        if token_created:
            self.tokens_migrados += 1
            if self.verbose:
                self.stdout.write(f"    🔑 Token migrado")
        
        # Cria permissões padrão (acesso total para compatibilidade)
        if not self.dry_run:
            permissao, perm_created = UsuarioContaGooglePermissao.objects.get_or_create(
                usuario=usuario,
                conta_google=conta_google,
                defaults={
                    'pode_visualizar': True,
                    'pode_criar': True,
                    'pode_editar': True,
                    'pode_excluir': True,
                    'criado_por': None,  # Migração automática
                }
            )
        else:
            perm_created = True  # Simula criação
        
        if perm_created:
            self.permissoes_criadas += 1
            if self.verbose:
                self.stdout.write(f"    🛡️  Permissão criada")
        
        # Marca token legado como migrado
        if not self.dry_run:
            token_legado.migrado = True
            token_legado.data_migracao = timezone.now()
            token_legado.save(update_fields=['migrado', 'data_migracao'])

    def _get_conta_usuario(self, usuario):
        """
        Retorna a conta associada ao usuário.
        ADAPTAR esta função baseado na lógica do seu sistema!
        
        Exemplos possíveis:
        - return usuario.profile.conta
        - return Conta.objects.filter(usuarios=usuario).first()
        - Usar grupos do Django, etc.
        """
        
        # 🚨 IMPLEMENTAR: Lógica específica do seu sistema
        # Por enquanto, tentamos algumas abordagens comuns:
        
        # Abordagem 1: Se há um profile com conta
        if hasattr(usuario, 'profile') and hasattr(usuario.profile, 'conta'):
            return usuario.profile.conta
        
        # Abordagem 2: Se usuário pertence a grupo relacionado a conta
        # (assumindo que nome do grupo == nome da conta)
        grupos_usuario = usuario.groups.values_list('name', flat=True)
        for grupo_nome in grupos_usuario:
            conta = Conta.objects.filter(nome__iexact=grupo_nome).first()
            if conta:
                return conta
        
        # Abordagem 3: Usar a primeira conta com google_calendar_id (fallback)
        # Útil para instalações pequenas com uma conta principal
        primeira_conta = Conta.objects.exclude(
            google_calendar_id__isnull=True
        ).exclude(
            google_calendar_id__exact=''
        ).first()
        
        if primeira_conta and self.verbose:
            self.stdout.write(
                f"    ⚠️  Usando conta padrão para {usuario.username}: {primeira_conta.nome}"
            )
        
        return primeira_conta

    def _criar_relatorio_final(self):
        """Cria relatório final da migração"""
        self.stdout.write("\n" + "="*60)
        self.stdout.write("📊 RELATÓRIO DE MIGRAÇÃO")
        self.stdout.write("="*60)
        
        # Contas Google criadas
        total_contas_google = ContaGoogleCalendar.objects.count() if not self.dry_run else self.contas_migradas
        self.stdout.write(f"📋 Contas Google Calendar: {total_contas_google}")
        
        # Tokens migrados
        total_tokens = TokenGoogleCalendar.objects.count() if not self.dry_run else self.tokens_migrados
        self.stdout.write(f"🔑 Tokens migrados: {total_tokens}")
        
        # Permissões criadas
        total_permissoes = UsuarioContaGooglePermissao.objects.count() if not self.dry_run else self.permissoes_criadas
        self.stdout.write(f"🛡️  Permissões criadas: {total_permissoes}")
        
        # Tokens legado pendentes
        if not self.dry_run:
            tokens_pendentes = GoogleApiToken.objects.filter(migrado=False).count()
            if tokens_pendentes > 0:
                self.stdout.write(
                    self.style.WARNING(f"⚠️  Tokens legado não migrados: {tokens_pendentes}")
                )
        
        self.stdout.write("\n📝 PRÓXIMOS PASSOS:")
        self.stdout.write("1. Verificar dados migrados no Django Admin")
        self.stdout.write("2. Configurar permissões específicas conforme necessário")
        self.stdout.write("3. Testar funcionalidade com usuários finais")
        self.stdout.write("4. Atualizar documentação para nova arquitetura")
        
        if self.dry_run:
            self.stdout.write(
                self.style.SUCCESS("\n✅ Simulação concluída - Execute sem --dry-run para aplicar")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("\n🎉 Migração concluída com sucesso!")
            )