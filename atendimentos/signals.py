from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from .models import Atendimento, LogDeAtividade, Tramitacao, PerfilUsuario, SolicitacaoAgenda, Notificacao
from .request_middleware import get_current_user
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.contrib.sites.models import Site


# Sinal para Atendimento (Criação e Edição)
@receiver(post_save, sender=Atendimento)
def handle_atendimento_criacao(sender, instance, created, **kwargs):
    if created:
        user = get_current_user() or User.objects.filter(is_superuser=True).first()

        # Notificação para o responsável INTERNO
        if instance.responsavel and instance.responsavel.email:
            context_interno = {
                'nome_responsavel': instance.responsavel.first_name or instance.responsavel.username,
                'protocolo': instance.protocolo,
                'titulo': instance.titulo,
                'nome_municipe': instance.municipe.nome_completo,
                'link_atendimento': f"https://gabinete.mogidascruzes.sp.gov.br/atendimentos/{instance.id}"
            }
            html_message = render_to_string('emails/notificacao_novo_atendimento.html', context_interno)
            send_mail(
                f"[SIGA] Novo Atendimento: {instance.protocolo}",
                "Um novo atendimento foi direcionado para sua responsabilidade.",
                'comunicacao.gabinete@mogidascruzes.sp.gov.br',
                [instance.responsavel.email],
                html_message=html_message
            )

        # --- CORREÇÃO: Notificação para o MUNÍCIPE com todos os dados dinâmicos ---
        
        # 1. Pega o primeiro e-mail da nova lista de e-mails
        municipe_email_principal = instance.municipe.emails[0].get('email') if instance.municipe and instance.municipe.emails else None
        
        if municipe_email_principal:
            # 2. Pega a conta e os dados de personalização
            conta = instance.conta
            site_domain = Site.objects.get_current().domain
            protocol = 'https' # Use 'http' em ambiente de desenvolvimento se necessário

            nome_instituicao = "Prefeitura Municipal" # Valor Padrão
            brasao_url = ''
            logo_conta_url = ''

            if conta:
                nome_instituicao = conta.nome_instituicao or nome_instituicao
                if conta.brasao_instituicao:
                    brasao_url = f"{protocol}://{site_domain}{conta.brasao_instituicao.url}"
                if conta.logo_conta:
                    logo_conta_url = f"{protocol}://{site_domain}{conta.logo_conta.url}"

            # 3. Monta o contexto completo para o template do e-mail
            context_externo = {
                'nome_municipe': instance.municipe.nome_completo,
                'protocolo': instance.protocolo,
                'titulo': instance.titulo,
                'data_criacao': instance.data_criacao.strftime('%d/%m/%Y às %H:%M'),
                'nome_instituicao': nome_instituicao,
                'brasao_url': brasao_url,
                'logo_conta_url': logo_conta_url,
            }
            html_message = render_to_string('emails/confirmacao_protocolo.html', context_externo)
            
            send_mail(
                f"Atendimento Registrado - Protocolo: {instance.protocolo}",
                f"Seu atendimento sobre '{instance.titulo}' foi registrado com o protocolo {instance.protocolo}.",
                settings.DEFAULT_FROM_EMAIL,
                [municipe_email_principal],
                html_message=html_message
            )
        
        # --- FIM DA CORREÇÃO ---

        # Lógica de notificação no "sininho" e Log (sem alterações)
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
    user = get_current_user() or User.objects.filter(is_superuser=True).first()
    detalhes = f"Atendimento com protocolo {instance.protocolo} (Título: {instance.titulo}) foi deletado pelo usuário {user.username if user else 'Sistema'}."
    LogDeAtividade.objects.create(usuario=user, acao='DELECAO', detalhes=detalhes, content_type=None, object_id=instance.id)

# Sinal para Tramitação (Criação e Edição)
@receiver(post_save, sender=Tramitacao)
def handle_tramitacao_save(sender, instance, created, **kwargs):
    if created:
        user = get_current_user() or User.objects.filter(is_superuser=True).first()
        atendimento = instance.atendimento

        # 2. Criar o log de atividade
        LogDeAtividade.objects.create(
            usuario=user,
            acao='TRAMITACAO',
            detalhes=f"O usuário '{user.username if user else 'Sistema'}' adicionou o andamento: '{instance.despacho[:50]}...' ao protocolo {atendimento.protocolo}.",
            content_object=atendimento
        )

# Sinal para Tramitação (Deleção)
@receiver(post_delete, sender=Tramitacao)
def log_tramitacao_delete(sender, instance, **kwargs):
    user = get_current_user() or User.objects.filter(is_superuser=True).first()
    detalhes = f"O usuário '{user.username if user else 'Sistema'}' excluiu o andamento '{instance.despacho[:50]}...' do protocolo {instance.atendimento.protocolo}."
    LogDeAtividade.objects.create(usuario=user, acao='DELECAO_TRAMITACAO', detalhes=detalhes, content_object=instance.atendimento)
    

@receiver(post_save, sender=SolicitacaoAgenda)
def notificar_agenda_confirmada(sender, instance, created, **kwargs):
    if not created and instance.status == 'AGENDADO':
        solicitante_email_principal = instance.solicitante.emails[0].get('email') if instance.solicitante and instance.solicitante.emails else None
        
        if solicitante_email_principal:
            try:
                # --- CORREÇÃO 2: Buscar os dados de personalização da Conta ---
                conta = instance.conta
                site_domain = Site.objects.get_current().domain
                protocol = 'https' # Ou 'http' se necessário

                nome_instituicao = "Prefeitura Municipal" # Valor Padrão
                brasao_url = ''
                logo_conta_url = ''

                if conta:
                    nome_instituicao = conta.nome_instituicao or nome_instituicao
                    if conta.brasao_instituicao:
                        brasao_url = f"{protocol}://{site_domain}{conta.brasao_instituicao.url}"
                    if conta.logo_conta:
                        logo_conta_url = f"{protocol}://{site_domain}{conta.logo_conta.url}"

                # 3. Monta o contexto completo para o template do e-mail
                context = {
                    'nome_municipe': instance.solicitante.nome_completo,
                    'assunto': instance.assunto,
                    'data_agendada': instance.data_agendada.strftime('%d/%m/%Y às %H:%M') if instance.data_agendada else "A ser confirmado",
                    'nome_gabinete': instance.conta.nome if instance.conta else "Não informado",
                    'nome_instituicao': nome_instituicao,
                    'brasao_url': brasao_url,
                    'logo_conta_url': logo_conta_url,
                }

                # O resto da lógica de envio de e-mail continua a mesma
                html_message = render_to_string('emails/confirmacao_agenda.html', context)
                plain_message = f"Sua reunião sobre '{context['assunto']}' foi agendada para {context['data_agendada']} no gabinete {context['nome_gabinete']}."

                send_mail(
                    f"Reunião Agendada: {instance.assunto}",
                    plain_message,
                    settings.DEFAULT_FROM_EMAIL,
                    [solicitante_email_principal],
                    html_message=html_message
                )
            except Exception as e:
                print(f"ERRO ao enviar e-mail de confirmação de agenda: {e}")

@receiver(post_save, sender=SolicitacaoAgenda)
def enviar_email_confirmacao_reserva(sender, instance, created, **kwargs):
    """
    Envia um e-mail de confirmação quando uma 'Reserva Rápida' é criada.
    Uma reserva rápida é identificada por ser criada já com o status 'AGENDADO'.
    """
    # A mágica acontece aqui: só dispara se for um registro NOVO (created=True)
    # e se o status for 'AGENDADO'
    if created and instance.status == 'AGENDADO':
        solicitante = instance.solicitante
        
        # Verifica se o solicitante tem um e-mail cadastrado
        if solicitante and solicitante.email:
            try:
                conta = instance.conta
                brasao_url = request.build_absolute_uri(conta.brasao_instituicao.url) if conta and conta.brasao_instituicao else ''
                logo_conta_url = request.build_absolute_uri(conta.logo_conta.url) if conta and conta.logo_conta else ''
                contexto = {
                    'nome_solicitante': solicitante.nome_completo,
                    'assunto': instance.assunto,
                    'nome_espaco': instance.espaco.nome if instance.espaco else 'Não especificado',
                    'data_agendada': instance.data_agendada.strftime('%d/%m/%Y às %H:%M'),
                    'data_agendada_fim': instance.data_agendada_fim.strftime('%H:%M'),
                    'brasao_url': brasao_url,
                    'logo_conta_url': logo_conta_url
                }

                html_message = render_to_string('emails/confirmacao_reserva_espaco.html', contexto)

                send_mail(
                    subject=f"Reserva Confirmada: {instance.assunto}",
                    message=f"Sua reserva para '{instance.assunto}' no espaço '{contexto['nome_espaco']}' foi confirmada.",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[solicitante.email],
                    html_message=html_message
                )
            except Exception as e:
                # Em caso de falha, o sistema não quebra, apenas registra o erro (idealmente em um log)
                print(f"ERRO ao enviar e-mail de confirmação de reserva para {solicitante.email}: {e}")