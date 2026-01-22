# atendimentos/utils.py
import os
from django.conf import settings
from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags
from email.mime.image import MIMEImage

def enviar_email_com_cid(assunto, destinatarios, template, contexto, conta=None):
    """
    Envia e-mail com imagens (Brasão/Logo) embutidas via CID.
    """
    imagens_para_anexar = []
    
    # Define valores padrão para evitar erros no template
    contexto['brasao_url'] = ""
    contexto['logo_conta_url'] = ""
    contexto['nome_instituicao'] = conta.nome_instituicao if conta and conta.nome_instituicao else "Prefeitura Municipal"

    # 1. Brasão
    if conta and conta.brasao_instituicao:
        try:
            if os.path.exists(conta.brasao_instituicao.path):
                imagens_para_anexar.append({
                    'path': conta.brasao_instituicao.path,
                    'cid': 'brasao_instituicao',
                    'nome': 'brasao.png'
                })
                contexto['brasao_url'] = "cid:brasao_instituicao"
        except Exception:
            pass

    # 2. Logo da Conta
    if conta and conta.logo_conta:
        try:
            if os.path.exists(conta.logo_conta.path):
                imagens_para_anexar.append({
                    'path': conta.logo_conta.path,
                    'cid': 'logo_conta',
                    'nome': 'logo.png'
                })
                contexto['logo_conta_url'] = "cid:logo_conta"
        except Exception:
            pass

    # 3. Renderiza
    html_content = render_to_string(template, contexto)
    text_content = strip_tags(html_content)

    # 4. Cria e-mail
    msg = EmailMultiAlternatives(
        subject=assunto,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=destinatarios
    )
    msg.attach_alternative(html_content, "text/html")

    # 5. Anexa imagens
    if imagens_para_anexar:
        msg.mixed_subtype = 'related'
        for img in imagens_para_anexar:
            try:
                with open(img['path'], 'rb') as f:
                    mime_img = MIMEImage(f.read())
                    mime_img.add_header('Content-ID', f"<{img['cid']}>")
                    mime_img.add_header('Content-Disposition', 'inline', filename=img['nome'])
                    msg.attach(mime_img)
            except Exception as e:
                print(f"Erro ao anexar {img['nome']}: {e}")

    # 6. Envia
    try:
        msg.send()
    except Exception as e:
        print(f"ERRO ao enviar e-mail: {e}")