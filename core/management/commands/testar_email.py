from django.core.management.base import BaseCommand, CommandError
from django.core.mail import send_mail
from django.conf import settings

class Command(BaseCommand):
    help = 'Envia um e-mail de teste para verificar a configuração SMTP.'

    def add_arguments(self, parser):
        # Adiciona um argumento obrigatório: o e-mail de destino para o teste
        parser.add_argument('email_destinatario', type=str, help='O endereço de e-mail para onde enviar o teste.')

    def handle(self, *args, **options):
        destinatario = options['email_destinatario']
        remetente = settings.EMAIL_HOST_USER

        self.stdout.write(self.style.WARNING(f'Tentando enviar e-mail de teste de "{remetente}" para "{destinatario}"...'))
        self.stdout.write(f'Usando o servidor: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}')

        try:
            # Tenta enviar o e-mail usando as configurações do settings.py
            send_mail(
                subject='Teste de Conexão SMTP - SIGA',
                message='Se você recebeu este e-mail, a configuração de envio está funcionando corretamente.',
                from_email=remetente,
                recipient_list=[destinatario],
                fail_silently=False,  # Importante: False para que qualquer erro seja levantado
            )
            self.stdout.write(self.style.SUCCESS('E-mail de teste enviado com sucesso! Verifique a caixa de entrada do destinatário.'))
            self.stdout.write(self.style.SUCCESS('=> Conclusão: Suas credenciais no .env estão corretas. O problema está no ambiente do Celery.'))

        except Exception as e:
            # Se ocorrer um erro, ele será capturado e exibido de forma clara.
            self.stdout.write(self.style.ERROR('Falha ao enviar o e-mail.'))
            raise CommandError(f'Ocorreu um erro: {e}')