# eventos/utils.py (exemplo conceitual)
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
# Recomendação: use a biblioteca WeasyPrint para converter HTML em PDF
# pip install WeasyPrint
# from weasyprint import HTML

def gerar_e_enviar_certificado(presenca):
    contexto = {'presenca': presenca}
    html_string = render_to_string('certificados/template_certificado.html', contexto)

    # 2. Converte o HTML para PDF em memória
    # html = HTML(string=html_string)
    # pdf = html.write_pdf()

    # 3. Envia o email com o PDF anexo
    email = EmailMessage(
        f"Seu certificado do evento: {presenca.evento.nome}",
        "Olá! Obrigado por participar. Seu certificado está em anexo.",
        "nao-responda@seu-dominio.com.br",
        [presenca.email]
    )
    # email.attach(f'certificado_{presenca.id}.pdf', pdf, 'application/pdf')
    # email.send()
    pass