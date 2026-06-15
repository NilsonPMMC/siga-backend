import os
from typing import Dict

from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.loader import render_to_string
from django.template import Context, Template, TemplateSyntaxError
from django.utils import timezone
from email.mime.image import MIMEImage

from atendimentos.models import (
    AutomacaoAniversarioConta,
    CampanhaDestinatario,
    CampanhaEmail,
    CampanhaLogEnvio,
    PerfilMunicipe,
)


def extract_primary_email(municipe):
    emails_raw = municipe.emails or []
    if not isinstance(emails_raw, list):
        return None
    for item in emails_raw:
        if isinstance(item, dict):
            candidate = (item.get("email") or "").strip().lower()
        else:
            candidate = str(item).strip().lower()
        if not candidate:
            continue
        try:
            validate_email(candidate)
            return candidate
        except ValidationError:
            continue
    return None


def extract_primary_phone(municipe):
    """Extrai telefone principal tolerando JSON legado (str ou dict)."""
    telefones_raw = municipe.telefones or []
    if not isinstance(telefones_raw, list):
        return ""
    for item in telefones_raw:
        if isinstance(item, dict):
            candidate = (item.get("numero") or item.get("telefone") or "").strip()
        else:
            candidate = str(item).strip()
        if candidate:
            return candidate
    return ""


def _render_campaign_templates(campanha, contexto):
    try:
        assunto = Template(campanha.assunto_template).render(Context(contexto)).strip()
        corpo_txt = Template(campanha.corpo_template).render(Context(contexto)).strip()
    except TemplateSyntaxError as exc:
        raise ValueError(f"Template inválido da campanha '{campanha.nome}': {exc}") from exc
    if not assunto:
        assunto = "Comunicado"
    return assunto, corpo_txt


def _open_smtp_for_campaign(campanha):
    try:
        automacao = campanha.conta.automacao_aniversario
    except AutomacaoAniversarioConta.DoesNotExist as exc:
        raise ValueError(
            f"Conta '{campanha.conta.nome}' sem configuração SMTP na automação de aniversário."
        ) from exc

    smtp_user = os.getenv(automacao.env_var_smtp_user or "")
    smtp_pass = os.getenv(automacao.env_var_smtp_pass or "")
    if not smtp_user or not smtp_pass:
        raise ValueError(
            f"Credenciais SMTP ausentes para conta '{campanha.conta.nome}' "
            f"({automacao.env_var_smtp_user}/{automacao.env_var_smtp_pass})."
        )

    conn = get_connection(
        backend="django.core.mail.backends.smtp.EmailBackend",
        host=automacao.smtp_host,
        port=automacao.smtp_port,
        username=smtp_user,
        password=smtp_pass,
        use_tls=automacao.smtp_use_tls,
        use_ssl=automacao.smtp_use_ssl,
        fail_silently=False,
    )
    conn.open()
    from_email = automacao.from_email or smtp_user
    return conn, from_email


@transaction.atomic
def prepare_campaign_recipients(campanha: CampanhaEmail) -> Dict[str, int]:
    perfis = PerfilMunicipe.objects.select_related("municipe", "conta", "categoria").filter(
        ativo=True,
        conta=campanha.conta,
        municipe__ativo=True,
    )
    if campanha.categorias.exists():
        perfis = perfis.filter(categoria__in=campanha.categorias.all())
    perfis = perfis.order_by("municipe__nome_completo")

    CampanhaDestinatario.objects.filter(campanha=campanha).delete()

    dedup = set()
    criados = 0
    sem_email = 0
    for perfil in perfis:
        chave = (perfil.conta_id, perfil.municipe_id)
        if chave in dedup:
            continue
        dedup.add(chave)

        email = extract_primary_email(perfil.municipe)
        if not email:
            sem_email += 1
            continue

        CampanhaDestinatario.objects.create(
            campanha=campanha,
            municipe=perfil.municipe,
            conta=perfil.conta,
            categoria=perfil.categoria,
            email_destino=email,
        )
        criados += 1

    campanha.total_destinatarios = criados
    campanha.save(update_fields=["total_destinatarios", "data_atualizacao"])
    return {"criados": criados, "sem_email": sem_email}


def process_campaign_send(campanha: CampanhaEmail, limit: int | None = None) -> Dict[str, int]:
    if campanha.status == "CANCELADA":
        return {"enviados": 0, "falhas": 0}

    campanha.status = "PROCESSANDO"
    campanha.save(update_fields=["status", "data_atualizacao"])

    destinatarios_qs = campanha.destinatarios.select_related("municipe", "conta", "categoria").all()
    if not destinatarios_qs.exists():
        prepare_campaign_recipients(campanha)
        destinatarios_qs = campanha.destinatarios.select_related("municipe", "conta", "categoria").all()

    if limit is not None and limit > 0:
        destinatarios_qs = destinatarios_qs[:limit]
    destinatarios = list(destinatarios_qs)

    sent = 0
    failed = 0
    smtp_conn = None
    from_email = None
    try:
        smtp_conn, from_email = _open_smtp_for_campaign(campanha)
        for dest in destinatarios:
            contexto = {
                "nome_completo": dest.municipe.nome_completo,
                "conta_nome": dest.conta.nome,
                "conta_nome_titular": dest.conta.nome_titular or "",
                "categoria_nome": dest.categoria.nome if dest.categoria else "",
                "campanha_nome": campanha.nome,
            }
            try:
                assunto, corpo_txt = _render_campaign_templates(campanha, contexto)
                to_addr = campanha.email_teste or dest.email_destino
                if campanha.email_teste:
                    assunto = f"[TESTE] {assunto}"

                corpo_html = (
                    corpo_txt.replace(chr(10), "<br>")
                )
                corpo_html = render_to_string(
                    "atendimentos/emails/campanha_email_base.html",
                    {
                        "assunto": assunto,
                        "corpo_html": corpo_html,
                        "possui_arte": bool(campanha.arte),
                    },
                )

                msg = EmailMultiAlternatives(
                    subject=assunto,
                    body=corpo_txt,
                    from_email=from_email,
                    to=[to_addr],
                    connection=smtp_conn,
                )
                msg.attach_alternative(corpo_html, "text/html")
                if campanha.arte:
                    with campanha.arte.open("rb") as f:
                        arte_img = MIMEImage(f.read())
                    arte_img.add_header("Content-ID", "<arte_campanha>")
                    arte_img.add_header("Content-Disposition", "inline", filename="arte-campanha")
                    msg.mixed_subtype = "related"
                    msg.attach(arte_img)
                if campanha.anexo:
                    msg.attach_file(campanha.anexo.path)
                msg.send()

                CampanhaLogEnvio.objects.create(
                    campanha=campanha,
                    destinatario=dest,
                    email_real_enviado=to_addr,
                    status="SUCESSO",
                )
                sent += 1
            except Exception as exc:
                CampanhaLogEnvio.objects.create(
                    campanha=campanha,
                    destinatario=dest,
                    email_real_enviado=to_addr,
                    status="FALHA",
                    erro=str(exc),
                )
                failed += 1
    finally:
        if smtp_conn:
            try:
                smtp_conn.close()
            except Exception:
                pass

    campanha.total_enviados = sent
    campanha.total_falhas = failed
    campanha.ultima_execucao_em = timezone.now()
    campanha.status = "CONCLUIDA" if failed == 0 else "ERRO"
    campanha.save(
        update_fields=[
            "total_enviados",
            "total_falhas",
            "ultima_execucao_em",
            "status",
            "data_atualizacao",
        ]
    )
    return {"enviados": sent, "falhas": failed}
