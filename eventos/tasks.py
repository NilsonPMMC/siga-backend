import os
import locale
from celery import shared_task
from django.core.mail import EmailMultiAlternatives, EmailMessage
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as DjangoValidationError
from email.mime.image import MIMEImage
from weasyprint import HTML
from django.template.loader import render_to_string
from django.conf import settings
from .models import Comunicacao, LogDeEnvio, ListaPresenca, EmailSupressao

def _emails_validos_do_municipe(municipe):
    """
    Extrai e valida e-mails do munícipe.
    Retorna lista de e-mails normalizados (lowercase) e válidos sintaticamente.
    """
    if not municipe.emails or not isinstance(municipe.emails, list):
        return []
    
    emails_validos = []
    for item in municipe.emails:
        if isinstance(item, dict):
            email_raw = item.get('email', '')
        else:
            email_raw = str(item)
        
        email = email_raw.strip().lower()
        if not email:
            continue
        
        try:
            validate_email(email)
            emails_validos.append(email)
        except DjangoValidationError:
            continue
    
    # Deduplica mantendo ordem
    vistos = set()
    resultado = []
    for email in emails_validos:
        if email not in vistos:
            vistos.add(email)
            resultado.append(email)
    
    return resultado


def _email_esta_suprimido(email_normalizado):
    """
    Verifica se o e-mail está suprimido (bloqueado) no banco.
    Retorna tupla: (bool_suprimido, motivo_str)
    """
    supressao = EmailSupressao.objects.filter(
        email=email_normalizado,
        status='ativo'
    ).first()
    
    if supressao:
        return (True, f"E-mail suprimido: {supressao.get_motivo_display()}")
    return (False, None)

def enviar_destinatario_comunicacao(comunicacao, destinatario, registrar_log=True):
    """
    Envia a comunicação para um destinatário (todos os e-mails válidos não suprimidos).
    Retorna tupla: (status, detalhe_erro_ou_none)
    """
    lista_emails = _emails_validos_do_municipe(destinatario.municipe)
    if not lista_emails:
        detalhe = 'Munícipe não possui e-mail cadastrado.'
        if registrar_log:
            LogDeEnvio.objects.create(
                comunicacao=comunicacao,
                destinatario=destinatario,
                status='falha',
                detalhe_erro=detalhe
            )
        return ('falha', detalhe)

    # Filtra e-mails suprimidos
    emails_permitidos = []
    emails_suprimidos = []
    for email in lista_emails:
        suprimido, motivo = _email_esta_suprimido(email)
        if suprimido:
            emails_suprimidos.append((email, motivo))
        else:
            emails_permitidos.append(email)
    
    if not emails_permitidos:
        detalhe = f"Todos os e-mails do munícipe estão suprimidos: {', '.join([e for e, _ in emails_suprimidos])}"
        if registrar_log:
            LogDeEnvio.objects.create(
                comunicacao=comunicacao,
                destinatario=destinatario,
                status='falha',
                detalhe_erro=detalhe
            )
        return ('falha', detalhe)

    try:
        for email_addr in emails_permitidos:
            corpo_html_personalizado = comunicacao.descricao.replace('{{ nome_completo }}', destinatario.municipe.nome_completo)

            if comunicacao.arte:
                corpo_html_personalizado += '<br><br><img src="cid:arte_comunicacao" style="max-width: 600px;">'

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

        if registrar_log:
            LogDeEnvio.objects.create(comunicacao=comunicacao, destinatario=destinatario, status='sucesso')
        return ('sucesso', None)
    except Exception as e:
        detalhe = str(e)
        if registrar_log:
            LogDeEnvio.objects.create(
                comunicacao=comunicacao,
                destinatario=destinatario,
                status='falha',
                detalhe_erro=detalhe
            )
        return ('falha', detalhe)

@shared_task
def enviar_comunicacao_em_massa(comunicacao_id):
    try:
        comunicacao = Comunicacao.objects.get(id=comunicacao_id)
    except Comunicacao.DoesNotExist:
        return f"Comunicação com ID {comunicacao_id} não encontrada."

    # only() evita carregar categoria_id (removido de Municipe; evita erro se worker estiver com código antigo)
    destinatarios = (
        comunicacao.destinatarios
        .select_related('municipe')
        .only('id', 'comunicacao_id', 'municipe_id', 'municipe__id', 'municipe__nome_completo', 'municipe__emails')
        .all()
    )

    sucessos = 0
    falhas = 0

    for destinatario in destinatarios:
        status_envio, _ = enviar_destinatario_comunicacao(comunicacao, destinatario, registrar_log=True)
        if status_envio == 'sucesso':
            sucessos += 1
        else:
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