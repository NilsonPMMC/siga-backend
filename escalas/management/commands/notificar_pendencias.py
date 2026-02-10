# escalas/management/commands/notificar_pendencias.py

from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone
from django.utils.crypto import get_random_string
from datetime import timedelta
from django.conf import settings
from escalas.models import EscalaPeriodo, EscalaRegistro
from atendimentos.models import Conta
from email.mime.image import MIMEImage
import os

class Command(BaseCommand):
    help = 'Notifica secretarias pendentes 3 dias antes do início'

    # --- NOVO MÉTODO AUXILIAR PARA LOG COM DATA ---
    def log(self, mensagem, style_func=None):
        timestamp = timezone.localtime().strftime('%d/%m/%Y %H:%M:%S')
        texto_formatado = f"[{timestamp}] {mensagem}"
        
        if style_func:
            self.stdout.write(style_func(texto_formatado))
        else:
            self.stdout.write(texto_formatado)

    def handle(self, *args, **options):
        hoje = timezone.localdate()
        limite = hoje + timedelta(days=3)

        periodos = EscalaPeriodo.objects.filter(
            ativo=True, 
            data_inicio__gte=hoje, 
            data_inicio__lte=limite
        )

        if not periodos.exists():
            # Usa self.log em vez de self.stdout.write
            self.log(f'Nenhum período iniciando entre {hoje} e {limite}.')
            return

        contas_obrigatorias = Conta.objects.filter(participa_escala=True)
        emails_enviados = 0

        caminho_logo = os.path.join(settings.BASE_DIR, 'staticfiles', 'images', 'logo-brasao-prefeitura.png')
        if not os.path.exists(caminho_logo):
             caminho_logo = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-brasao-prefeitura.png')

        for periodo in periodos:
            self.log(f"--- Processando Período: {periodo.nome} (Início: {periodo.data_inicio}) ---")
            
            for conta in contas_obrigatorias:
                if EscalaRegistro.objects.filter(periodo=periodo, conta=conta).exists():
                    continue
                    
                perfis = conta.perfilusuario_set.filter(
                    usuario__groups__name__in=['Escalas', 'Gestor de Escalas'],
                    usuario__is_active=True
                ).select_related('usuario').distinct()
                
                if not perfis.exists():
                    continue

                for perfil in perfis:
                    usuario = perfil.usuario
                    if not usuario.email:
                        continue

                    try:
                        senha_provisoria = None
                        
                        if usuario.last_login is None:
                            senha_provisoria = get_random_string(length=8)
                            usuario.set_password(senha_provisoria)
                            usuario.save()
                            self.log(f" -> [Primeiro Acesso] Nova senha gerada para {usuario.username}")

                        contexto = {
                            'nome_usuario': usuario.first_name or usuario.username,
                            'nome_secretaria': conta.nome,
                            'nome_periodo': periodo.nome,
                            'data_inicio': periodo.data_inicio.strftime('%d/%m/%Y'),
                            'username': usuario.username,
                            'senha_provisoria': senha_provisoria,
                            'dias_restantes': (periodo.data_inicio - hoje).days
                        }

                        html_content = render_to_string('escalas/email/notificacao_pendencia.html', contexto)
                        text_content = strip_tags(html_content)

                        subject = f"⚠️ Pendência: Escala de Plantão - {periodo.nome}"
                        msg = EmailMultiAlternatives(subject, text_content, settings.DEFAULT_FROM_EMAIL, [usuario.email])
                        msg.attach_alternative(html_content, "text/html")

                        if os.path.exists(caminho_logo):
                            msg.mixed_subtype = 'related'
                            with open(caminho_logo, 'rb') as f:
                                img = MIMEImage(f.read())
                                img.add_header('Content-ID', '<logo_siga>')
                                img.add_header('Content-Disposition', 'inline', filename='logo.png')
                                msg.attach(img)

                        msg.send()
                        emails_enviados += 1
                        self.log(f" -> E-mail enviado para {usuario.email} ({conta.nome})")

                    except Exception as e:
                        self.log(f"Erro ao enviar para {usuario.email}: {e}", self.style.ERROR)

        self.log(f'Rotina finalizada. Total de notificações: {emails_enviados}', self.style.SUCCESS)