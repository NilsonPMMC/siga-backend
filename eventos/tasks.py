import os
import locale
from celery import shared_task
from django.core.mail import EmailMultiAlternatives, EmailMessage
from email.mime.image import MIMEImage
from weasyprint import HTML
from django.template.loader import render_to_string
from django.conf import settings
from .models import Comunicacao, LogDeEnvio, ListaPresenca

@shared_task
def enviar_comunicacao_em_massa(comunicacao_id):
    try:
        comunicacao = Comunicacao.objects.get(id=comunicacao_id)
    except Comunicacao.DoesNotExist:
        return f"Comunicação com ID {comunicacao_id} não encontrada."

    destinatarios = comunicacao.destinatarios.select_related('municipe').all()

    sucessos = 0
    falhas = 0

    for destinatario in destinatarios:
        # 1. Inicializa uma lista vazia para armazenar todos os e-mails do munícipe.
        lista_emails = []
        
        # 2. Verifica se o campo 'emails' existe e é uma lista, extraindo os e-mails.
        if destinatario.municipe.emails and isinstance(destinatario.municipe.emails, list):
            lista_emails = [e['email'] for e in destinatario.municipe.emails if e.get('email')]

        # Se a lista de e-mails estiver vazia, registra a falha.
        if not lista_emails:
            LogDeEnvio.objects.create(
                comunicacao=comunicacao,
                destinatario=destinatario,
                status='falha',
                detalhe_erro='Munícipe não possui e-mail cadastrado.'
            )
            falhas += 1
            continue

        try:
            # Itera sobre cada e-mail encontrado e envia uma mensagem individual
            for email_addr in lista_emails:
                corpo_html_personalizado = comunicacao.descricao.replace('{{ nome_completo }}', destinatario.municipe.nome_completo)
                
                if comunicacao.arte:
                    corpo_html_personalizado += f'<br><br><img src="cid:arte_comunicacao" style="max-width: 600px;">'

                # O campo 'to' agora recebe uma lista com um único e-mail por vez
                email = EmailMultiAlternatives(
                    subject=comunicacao.titulo,
                    body=corpo_html_personalizado,
                    to=[email_addr]
                )
                email.attach_alternative(corpo_html_personalizado, "text/html")

                if comunicacao.arte:
                    with comunicacao.arte.open('rb') as f:
                        arte_img = MIMEImage(f.read())
                        arte_img.add_header('Content-ID', '<arte_comunicacao>')
                        email.attach(arte_img)
                
                if comunicacao.anexo:
                    email.attach_file(comunicacao.anexo.path)

                email.send()

            # Se todos os e-mails do munícipe foram enviados, registra um único sucesso.
            LogDeEnvio.objects.create(comunicacao=comunicacao, destinatario=destinatario, status='sucesso')
            sucessos += 1

        except Exception as e:
            LogDeEnvio.objects.create(
                comunicacao=comunicacao,
                destinatario=destinatario,
                status='falha',
                detalhe_erro=str(e)
            )
            falhas += 1

    return f"Envio concluído. Sucessos: {sucessos}, Falhas: {falhas}."

@shared_task
def gerar_e_enviar_certificado(presenca_id):
    try:
        presenca = ListaPresenca.objects.select_related('evento', 'municipe', 'evento__conta').get(id=presenca_id)
    except ListaPresenca.DoesNotExist:
        return f"Registro de Presença com ID {presenca_id} não encontrado."

    if not presenca.email:
        return f"Participante {presenca.nome_completo} não possui e-mail para envio."

    conta = presenca.evento.conta
    
    logo_path = conta.logo_conta.path if conta.logo_conta else ''
    brasao_path = conta.brasao_instituicao.path if conta.brasao_instituicao else ''

    meses_pt_br = [
        'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
        'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'
    ]
    data_evento = presenca.evento.data_evento
    dia = data_evento.day
    mes = meses_pt_br[data_evento.month - 1]
    ano = data_evento.year
    data_formatada_pt_br = f'{dia} de {mes} de {ano}'

    #contexto_pdf = {
    #    'nome_completo': presenca.nome_completo,
    #   'nome_evento': presenca.evento.nome,
    #    'data_evento': data_formatada_pt_br,
    #    'logo_url': f'file://{logo_path}',
    #    'brasao_url': f'file://{brasao_path}',
    #}
    #html_string_pdf = render_to_string('eventos/certificados/template_certificado.html', contexto_pdf)

    #pdf_file = HTML(string=html_string_pdf, base_url=settings.BASE_DIR).write_pdf()

    contexto_email = {
        'nome_completo': presenca.nome_completo,
        'nome_evento': presenca.evento.nome,
        'logo_url': f"{settings.SITE_URL}{conta.logo_conta.url}" if conta.logo_conta else '',
        'brasao_url': f"{settings.SITE_URL}{conta.brasao_instituicao.url}" if conta.brasao_instituicao else '',
    }
    corpo_html_email = render_to_string('eventos/emails/email_certificado.html', contexto_email)

    try:
        email = EmailMessage(
            subject=f"Agradecemos sua participação no evento: {presenca.evento.nome}",
            body=corpo_html_email,
            to=[presenca.email]
        )
        email.content_subtype = "html" 
        #email.attach(
        #    f'Certificado - {presenca.evento.nome}.pdf',
        #    pdf_file,
        #    'application/pdf'
        #)
        email.send()
        return f"Certificado enviado com sucesso para {presenca.email}."
    except Exception as e:
        return f"Falha ao enviar e-mail para {presenca.email}: {e}"