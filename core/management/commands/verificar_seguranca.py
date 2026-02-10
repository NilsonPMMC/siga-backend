"""
Comando Django para verificar configurações de segurança.
Execute: python manage.py verificar_seguranca
"""
from django.core.management.base import BaseCommand
from django.conf import settings
import os
from pathlib import Path


class Command(BaseCommand):
    help = 'Verifica configurações de segurança do projeto'

    def add_arguments(self, parser):
        parser.add_argument(
            '--strict',
            action='store_true',
            help='Modo estrito: falha se encontrar problemas críticos',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('\n🔒 Verificação de Segurança - SIGA\n'))
        self.stdout.write('=' * 60 + '\n')

        errors = []
        warnings = []
        success = []

        # 1. Verificar arquivo .env
        env_path = Path(settings.BASE_DIR) / '.env'
        if not env_path.exists():
            errors.append('❌ Arquivo .env não encontrado! Crie um baseado em .env.example')
        else:
            success.append('✅ Arquivo .env encontrado')
            # Verificar permissões do arquivo .env (apenas em Unix/Linux)
            if os.name != 'nt':  # Não Windows
                stat_info = os.stat(env_path)
                mode = oct(stat_info.st_mode)[-3:]
                if mode != '600':
                    warnings.append(f'⚠️  Arquivo .env tem permissões {mode}. Recomendado: 600 (apenas leitura/escrita pelo dono)')

        # 2. Verificar SECRET_KEY
        default_key = 'django-insecure--7hk=jn*vw$wm*sd*6t=l0tkh(k5brj)_+un79yc)e9(805k4l'
        if settings.SECRET_KEY == default_key:
            errors.append('❌ SECRET_KEY está usando o valor padrão inseguro! Configure uma chave única no .env')
        else:
            success.append('✅ SECRET_KEY configurada (não é o valor padrão)')

        # 3. Verificar DEBUG
        if settings.DEBUG:
            if 'gabinete.mogidascruzes.sp.gov.br' in settings.ALLOWED_HOSTS:
                errors.append('❌ DEBUG está True em ambiente de produção! Configure DEBUG=False no .env')
            else:
                warnings.append('⚠️  DEBUG está True (aceitável apenas em desenvolvimento local)')
        else:
            success.append('✅ DEBUG está False (correto para produção)')

        # 4. Verificar banco de dados
        db_config = settings.DATABASES['default']
        if not db_config.get('PASSWORD'):
            errors.append('❌ Senha do banco de dados não configurada! Configure DB_PASSWORD no .env')
        else:
            success.append('✅ Senha do banco de dados configurada')

        # 5. Verificar configurações de SSL/HTTPS
        if 'gabinete.mogidascruzes.sp.gov.br' in settings.ALLOWED_HOSTS:
            if not settings.SECURE_SSL_REDIRECT:
                warnings.append('⚠️  SECURE_SSL_REDIRECT está False. Configure True em produção')
            else:
                success.append('✅ SECURE_SSL_REDIRECT está True')

            if not settings.SESSION_COOKIE_SECURE:
                warnings.append('⚠️  SESSION_COOKIE_SECURE está False. Configure True em produção')
            else:
                success.append('✅ SESSION_COOKIE_SECURE está True')

            if not settings.CSRF_COOKIE_SECURE:
                warnings.append('⚠️  CSRF_COOKIE_SECURE está False. Configure True em produção')
            else:
                success.append('✅ CSRF_COOKIE_SECURE está True')

        # 6. Verificar se .env está no Git (verifica .gitignore)
        gitignore_path = Path(settings.BASE_DIR) / '.gitignore'
        if gitignore_path.exists():
            gitignore_content = gitignore_path.read_text()
            if '.env' in gitignore_content:
                success.append('✅ Arquivo .env está no .gitignore')
            else:
                errors.append('❌ Arquivo .env NÃO está no .gitignore! Adicione .env ao .gitignore')
        else:
            warnings.append('⚠️  Arquivo .gitignore não encontrado')

        # 7. Verificar variáveis de ambiente críticas
        env_vars_required = [
            'SECRET_KEY',
            'DB_PASSWORD',
        ]
        
        env_vars_recommended = [
            'SMTP_USER',
            'SMTP_PASSWORD',
            'GOOGLE_CLIENT_ID',
            'GOOGLE_CLIENT_SECRET',
            'GEMINI_API_KEY',
        ]

        # Verificar variáveis obrigatórias
        missing_required = []
        for var in env_vars_required:
            if not os.environ.get(var):
                missing_required.append(var)
        
        if missing_required:
            errors.append(f'❌ Variáveis de ambiente obrigatórias não configuradas: {", ".join(missing_required)}')
        else:
            success.append('✅ Variáveis de ambiente obrigatórias configuradas')

        # Verificar variáveis recomendadas
        missing_recommended = []
        for var in env_vars_recommended:
            if not os.environ.get(var):
                missing_recommended.append(var)
        
        if missing_recommended:
            warnings.append(f'⚠️  Variáveis de ambiente recomendadas não configuradas: {", ".join(missing_recommended)}')

        # Exibir resultados
        self.stdout.write('\n📊 RESULTADOS:\n')
        
        if success:
            self.stdout.write(self.style.SUCCESS('\n✅ SUCESSOS:'))
            for msg in success:
                self.stdout.write(f'  {msg}')

        if warnings:
            self.stdout.write(self.style.WARNING('\n⚠️  AVISOS:'))
            for msg in warnings:
                self.stdout.write(f'  {msg}')

        if errors:
            self.stdout.write(self.style.ERROR('\n❌ ERROS CRÍTICOS:'))
            for msg in errors:
                self.stdout.write(f'  {msg}')

        # Resumo final
        self.stdout.write('\n' + '=' * 60)
        if errors:
            self.stdout.write(self.style.ERROR(f'\n❌ Encontrados {len(errors)} erro(s) crítico(s)!'))
            if options['strict']:
                self.stdout.write(self.style.ERROR('Modo estrito ativado. Corrija os erros antes de continuar.'))
                exit(1)
            else:
                self.stdout.write(self.style.WARNING('Corrija os erros antes de fazer deploy em produção.'))
        elif warnings:
            self.stdout.write(self.style.WARNING(f'\n⚠️  Verificação concluída com {len(warnings)} aviso(s).'))
            self.stdout.write(self.style.SUCCESS('Nenhum erro crítico encontrado.'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ Verificação concluída! Todas as configurações estão corretas.'))

        self.stdout.write('\n')
