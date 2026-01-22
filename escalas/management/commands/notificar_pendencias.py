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
from email.mime.image import MIMEImage # Necessário para o logo
import os

class Command(BaseCommand):
    help = 'Notifica secretarias pendentes (apenas grupo Escalas) e envia credenciais se necessário'

    def handle(self, *args, **options):
        hoje = timezone.localdate()
        limite = hoje + timedelta(days=3)

        periodos = EscalaPeriodo.objects.filter(ativo=True, data_inicio__gte=hoje, data_inicio__lte=limite)

        if not periodos.exists():
            self.stdout.write(self.style.SUCCESS('Nenhum período próximo.'))
            return

        contas_obrigatorias = Conta.objects.filter(participa_escala=True)
        emails_enviados = 0

        caminho_logo = os.path.join(settings.BASE_DIR, 'staticfiles', 'images', 'logo-brasao-prefeitura.png')
        if not os.path.exists(caminho_logo):
             caminho_logo = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-brasao-prefeitura.png')

        for periodo in periodos:
            self.stdout.write(f"--- Verificando: {periodo.nome} ---")
            
            for conta in contas_obrigatorias:
                if not EscalaRegistro.objects.filter(periodo=periodo, conta=conta).exists():
                    
                    # 1. FILTRO DE GRUPO: Só pega usuários do grupo 'Escalas' ou 'Gestor de Escalas'
                    perfis = conta.perfilusuario_set.filter(
                        usuario__groups__name__in=['Escalas', 'Gestor de Escalas']
                    ).select_related('usuario')
                    
                    if not perfis.exists():
                        self.stdout.write(self.style.WARNING(f"Conta {conta.nome} pendente, mas sem usuários no grupo 'Escalas'."))
                        continue

                    for perfil in perfis:
                        usuario = perfil.usuario
                        if usuario and usuario.email and usuario.is_active:
                            try:
                                senha_provisoria = None
                                
                                # 2. LÓGICA INTELIGENTE DE SENHA
                                # Só reseta se NUNCA logou (last_login is None)
                                if usuario.last_login is None:
                                    senha_provisoria = get_random_string(length=8)
                                    usuario.set_password(senha_provisoria)
                                    usuario.save()
                                    self.stdout.write(f" -> Senha gerada para {usuario.username} (Primeiro acesso)")

                                # Prepara dados
                                contexto = {
                                    'nome_usuario': usuario.first_name or usuario.username,
                                    'nome_secretaria': conta.nome,
                                    'nome_periodo': periodo.nome,
                                    'data_inicio': periodo.data_inicio.strftime('%d/%m/%Y'),
                                    'username': usuario.username,
                                    'senha_provisoria': senha_provisoria, # Se for None, o HTML esconde
                                }

                                # Renderiza
                                html_content = render_to_string('escalas/email/notificacao_pendencia.html', contexto)
                                text_content = strip_tags(html_content)

                                subject = f"⚠️ Pendência: Escala de Plantão - {periodo.nome}"
                                msg = EmailMultiAlternatives(subject, text_content, settings.DEFAULT_FROM_EMAIL, [usuario.email])
                                msg.attach_alternative(html_content, "text/html")

                                # 3. ANEXAR LOGO (CID)
                                if os.path.exists(caminho_logo):
                                    msg.mixed_subtype = 'related' # Importante para imagens inline
                                    with open(caminho_logo, 'rb') as f:
                                        img = MIMEImage(f.read())
                                        img.add_header('Content-ID', '<logo_siga>') # O ID usado no HTML
                                        img.add_header('Content-Disposition', 'inline', filename='logo.png')
                                        msg.attach(img)

                                msg.send()
                                emails_enviados += 1

                            except Exception as e:
                                self.stdout.write(self.style.ERROR(f"Erro ao processar {usuario.email}: {e}"))

        self.stdout.write(self.style.SUCCESS(f'Concluído. {emails_enviados} notificações enviadas.'))