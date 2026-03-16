import os
from django.conf import settings
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags
from email.mime.image import MIMEImage

from .models import Atendimento, LogDeAtividade, Tramitacao, SolicitacaoAgenda, Notificacao
from .request_middleware import get_current_user

# --- FUNÇÃO AUXILIAR PARA ENVIAR E-MAIL COM IMAGENS EMBUTIDAS (CID) ---
def enviar_email_com_cid(assunto, destinatarios, template, contexto, conta=None):
    """
    Função utilitária para preparar imagens (Brasão e Logo) como anexos inline (CID)
    e enviar o e-mail HTML.
    """
    imagens_para_anexar = []
    
    # Valores padrão para o contexto
    contexto['brasao_url'] = ""
    contexto['logo_conta_url'] = ""
    contexto['nome_instituicao'] = conta.nome_instituicao if conta and conta.nome_instituicao else "Prefeitura Municipal"

    # 1. Processar Brasão
    if conta and conta.brasao_instituicao:
        try:
            caminho_brasao = conta.brasao_instituicao.path
            if os.path.exists(caminho_brasao):
                imagens_para_anexar.append({
                    'path': caminho_brasao,
                    'cid': 'brasao_instituicao',
                    'nome': 'brasao.png'
                })
                contexto['brasao_url'] = "cid:brasao_instituicao"
        except Exception:
            pass # Se der erro no arquivo, segue sem imagem

    # 2. Processar Logo da Conta
    if conta and conta.logo_conta:
        try:
            caminho_logo = conta.logo_conta.path
            if os.path.exists(caminho_logo):
                imagens_para_anexar.append({
                    'path': caminho_logo,
                    'cid': 'logo_conta',
                    'nome': 'logo.png'
                })
                contexto['logo_conta_url'] = "cid:logo_conta"
        except Exception:
            pass

    # 3. Renderizar Templates
    html_content = render_to_string(template, contexto)
    text_content = strip_tags(html_content)

    # 4. Criar e-mail
    msg = EmailMultiAlternatives(
        subject=assunto,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=destinatarios
    )
    msg.attach_alternative(html_content, "text/html")

    # 5. Anexar imagens Inline
    if imagens_para_anexar:
        msg.mixed_subtype = 'related'
        for img_data in imagens_para_anexar:
            try:
                with open(img_data['path'], 'rb') as f:
                    img = MIMEImage(f.read())
                    img.add_header('Content-ID', f"<{img_data['cid']}>")
                    img.add_header('Content-Disposition', 'inline', filename=img_data['nome'])
                    msg.attach(img)
            except Exception as e:
                print(f"Erro ao anexar imagem {img_data['nome']}: {e}")

    # 6. Enviar
    try:
        msg.send()
    except Exception as e:
        print(f"ERRO ao enviar e-mail ({assunto}): {e}")


# --- SIGNALS ---

# Sinal para Atendimento (Criação e Edição)
@receiver(post_save, sender=Atendimento)
def handle_atendimento_criacao(sender, instance, created, **kwargs):
    # --- TRAVA DE SEGURANÇA PARA IMPORTAÇÃO ---
    if os.environ.get('SILENCE_SIGNALS'):
        return
    # ------------------------------------------

    if created:
        user = get_current_user() or User.objects.filter(is_superuser=True).first()

        # Notificações por e-mail de novo atendimento (interno e para o munícipe)
        # podem ser habilitadas via settings.ENVIAR_EMAILS_ATENDIMENTOS = True.
        if getattr(settings, 'ENVIAR_EMAILS_ATENDIMENTOS', False):
            # Notificação para o responsável INTERNO (Simples, sem imagens personalizadas)
            if instance.responsavel and instance.responsavel.email:
                context_interno = {
                    'nome_responsavel': instance.responsavel.first_name or instance.responsavel.username,
                    'protocolo': instance.protocolo,
                    'titulo': instance.titulo,
                    'nome_municipe': instance.municipe.nome_completo,
                    'link_atendimento': f"https://gabinete.mogidascruzes.sp.gov.br/atendimentos/{instance.id}"
                }
                html_message = render_to_string('emails/notificacao_novo_atendimento.html', context_interno)
                msg_interna = EmailMultiAlternatives(
                    f"[SIGA] Novo Atendimento: {instance.protocolo}",
                    strip_tags(html_message),
                    'comunicacao.gabinete@mogidascruzes.sp.gov.br',
                    [instance.responsavel.email]
                )
                msg_interna.attach_alternative(html_message, "text/html")
                msg_interna.send()

            # Notificação para o MUNÍCIPE usando CID
            municipe_email_principal = instance.municipe.emails[0].get('email') if instance.municipe and instance.municipe.emails else None
            
            if municipe_email_principal:
                context_externo = {
                    'nome_municipe': instance.municipe.nome_completo,
                    'protocolo': instance.protocolo,
                    'titulo': instance.titulo,
                    'data_criacao': instance.data_criacao.strftime('%d/%m/%Y às %H:%M'),
                }
                
                enviar_email_com_cid(
                    assunto=f"Atendimento Registrado - Protocolo: {instance.protocolo}",
                    destinatarios=[municipe_email_principal],
                    template='emails/confirmacao_protocolo.html',
                    contexto=context_externo,
                    conta=instance.conta
                )
        
        # Logs e Notificações internas (mantidos mesmo sem e-mail)
        if instance.responsavel:
            Notificacao.objects.create(
                usuario=instance.responsavel,
                mensagem=f"Novo atendimento recebido: {instance.protocolo}",
                link=f"/atendimentos/{instance.id}"
            )
        LogDeAtividade.objects.create(
            usuario=user,
            acao='CRIACAO',
            detalhes=f"Atendimento {instance.protocolo} criado.",
            content_object=instance
        )

# Sinal para Atendimento (Deleção)
@receiver(post_delete, sender=Atendimento)
def log_atendimento_delete(sender, instance, **kwargs):
    # --- TRAVA DE SEGURANÇA PARA IMPORTAÇÃO ---
    if os.environ.get('SILENCE_SIGNALS'):
        return
    # ------------------------------------------

    user = get_current_user() or User.objects.filter(is_superuser=True).first()
    detalhes = f"Atendimento com protocolo {instance.protocolo} (Título: {instance.titulo}) foi deletado pelo usuário {user.username if user else 'Sistema'}."
    LogDeAtividade.objects.create(usuario=user, acao='DELECAO', detalhes=detalhes, content_type=None, object_id=instance.id)

# Sinal para Tramitação (Criação e Edição)
@receiver(post_save, sender=Tramitacao)
def handle_tramitacao_save(sender, instance, created, **kwargs):
    # --- TRAVA DE SEGURANÇA PARA IMPORTAÇÃO ---
    if os.environ.get('SILENCE_SIGNALS'):
        return
    # ------------------------------------------

    atendimento = instance.atendimento
    # Marca atendimento para reprocessamento de IA quando novo despacho é inserido
    Atendimento.objects.filter(pk=atendimento.pk).update(auditoria_ia_status='PENDENTE')

    if created:
        user = get_current_user() or User.objects.filter(is_superuser=True).first()

        # Log de atividade
        LogDeAtividade.objects.create(
            usuario=user,
            acao='TRAMITACAO',
            detalhes=f"O usuário '{user.username if user else 'Sistema'}' adicionou o andamento: '{instance.despacho[:50]}...' ao protocolo {atendimento.protocolo}.",
            content_object=atendimento
        )

# Sinal para Tramitação (Deleção)
@receiver(post_delete, sender=Tramitacao)
def log_tramitacao_delete(sender, instance, **kwargs):
    # --- TRAVA DE SEGURANÇA PARA IMPORTAÇÃO ---
    if os.environ.get('SILENCE_SIGNALS'):
        return
    # ------------------------------------------

    user = get_current_user() or User.objects.filter(is_superuser=True).first()
    detalhes = f"O usuário '{user.username if user else 'Sistema'}' excluiu o andamento '{instance.despacho[:50]}...' do protocolo {instance.atendimento.protocolo}."
    LogDeAtividade.objects.create(usuario=user, acao='DELECAO_TRAMITACAO', detalhes=detalhes, content_object=instance.atendimento)
    

# Sinal para Agenda Confirmada
@receiver(post_save, sender=SolicitacaoAgenda)
def notificar_agenda_confirmada(sender, instance, created, **kwargs):
    # --- TRAVA DE SEGURANÇA PARA IMPORTAÇÃO ---
    if os.environ.get('SILENCE_SIGNALS'):
        return
    # ------------------------------------------

    if not created and instance.status == 'AGENDADO':
        # Notificações por e-mail de agenda confirmada podem ser habilitadas
        # via settings.ENVIAR_EMAILS_AGENDA = True.
        if getattr(settings, 'ENVIAR_EMAILS_AGENDA', False):
            solicitante_email_principal = instance.solicitante.emails[0].get('email') if instance.solicitante and instance.solicitante.emails else None
            
            if solicitante_email_principal:
                context = {
                    'nome_municipe': instance.solicitante.nome_completo,
                    'assunto': instance.assunto,
                    'data_agendada': instance.data_agendada.strftime('%d/%m/%Y às %H:%M') if instance.data_agendada else "A ser confirmado",
                    'nome_gabinete': instance.conta.nome if instance.conta else "Não informado",
                }

                enviar_email_com_cid(
                    assunto=f"Reunião Agendada: {instance.assunto}",
                    destinatarios=[solicitante_email_principal],
                    template='emails/confirmacao_agenda.html',
                    contexto=context,
                    conta=instance.conta
                )

# Sinal para Reserva de Espaço (Reserva Rápida)
@receiver(post_save, sender=SolicitacaoAgenda)
def enviar_email_confirmacao_reserva(sender, instance, created, **kwargs):
    # --- TRAVA DE SEGURANÇA PARA IMPORTAÇÃO ---
    if os.environ.get('SILENCE_SIGNALS'):
        return
    # ------------------------------------------

    if created and instance.status == 'AGENDADO':
        # Notificações por e-mail de reserva de espaço podem ser habilitadas
        # via settings.ENVIAR_EMAILS_AGENDA = True.
        if getattr(settings, 'ENVIAR_EMAILS_AGENDA', False):
            solicitante = instance.solicitante
            
            if solicitante and solicitante.email:
                contexto = {
                    'nome_solicitante': solicitante.nome_completo,
                    'assunto': instance.assunto,
                    'nome_espaco': instance.espaco.nome if instance.espaco else 'Não especificado',
                    'data_agendada': instance.data_agendada.strftime('%d/%m/%Y às %H:%M'),
                    'data_agendada_fim': instance.data_agendada_fim.strftime('%H:%M') if instance.data_agendada_fim else "",
                }

                enviar_email_com_cid(
                    assunto=f"Reserva Confirmada: {instance.assunto}",
                    destinatarios=[solicitante.email],
                    template='emails/confirmacao_reserva_espaco.html',
                    contexto=contexto,
                    conta=instance.conta
                )
