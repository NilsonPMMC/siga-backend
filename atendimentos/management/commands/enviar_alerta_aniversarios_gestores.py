import os
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import connection
from django.template.loader import render_to_string
from django.template import Context, Template, TemplateSyntaxError
from django.utils import timezone
from weasyprint import HTML

from atendimentos.models import AutomacaoAniversarioConta, Municipe, PerfilMunicipe


class Command(BaseCommand):
    help = (
        "Envia alerta consolidado de aniversariantes para usuários internos (gestores), "
        "com base nas configurações da Automação de Aniversário por Conta."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-anterior",
            dest="data_anterior",
            type=str,
            default=None,
            help="Data anterior no formato YYYY-MM-DD. O alerta usa o dia seguinte como data de aniversário.",
        )
        parser.add_argument(
            "--conta-id",
            dest="conta_id",
            type=int,
            default=None,
            help="Filtra execução para uma conta específica.",
        )
        parser.add_argument(
            "--email-teste",
            dest="email_teste",
            type=str,
            default=None,
            help="Força envio para um único e-mail de teste (sobrepõe destinatários do admin).",
        )
        parser.add_argument(
            "--send",
            action="store_true",
            dest="send",
            help="Executa envio real. Sem esta flag, roda em DRY-RUN.",
        )

    def _parse_data_base(self, data_anterior):
        if not data_anterior:
            return timezone.localdate()
        try:
            return datetime.strptime(data_anterior, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError(
                f"Formato inválido para --data-anterior: {data_anterior}. Use YYYY-MM-DD."
            ) from exc

    def _open_smtp_connection(self, automacao):
        smtp_user = os.getenv(automacao.env_var_smtp_user or "")
        smtp_pass = os.getenv(automacao.env_var_smtp_pass or "")
        if not smtp_user or not smtp_pass:
            raise CommandError(
                f"Credenciais SMTP ausentes para conta '{automacao.conta.nome}'. "
                f"Verifique {automacao.env_var_smtp_user} / {automacao.env_var_smtp_pass} no .env."
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

    def _render_alert_templates(self, automacao, context):
        try:
            subject = Template(automacao.alerta_assunto_template).render(Context(context)).strip()
            body_txt = Template(automacao.alerta_corpo_template).render(Context(context)).strip()
        except TemplateSyntaxError as exc:
            raise CommandError(
                f"Template de alerta inválido para conta '{automacao.conta.nome}': {exc}"
            ) from exc
        if not subject:
            subject = f"Relação de aniversariantes - {context['conta_nome']}"
        return subject, body_txt

    def _build_lista_aniversariantes_texto(self, aniversariantes):
        linhas = []
        for item in aniversariantes:
            linhas.append(
                f"- {item['nome_completo']} | Tel: {item['telefone_principal']} | "
                f"Cargo: {item['cargo']} | Categoria: {item['categoria']}"
            )
        return "\n".join(linhas)

    def _build_lista_aniversariantes_html(self, aniversariantes):
        itens = []
        for item in aniversariantes:
            itens.append(
                "<li style=\"margin-bottom:8px;\">"
                f"<strong>{item['nome_completo']}</strong><br>"
                f"<span style=\"font-size:12px;color:#555;\">"
                f"Tel: {item['telefone_principal']} · Cargo: {item['cargo']} · "
                f"Categoria: {item['categoria']}"
                "</span></li>"
            )
        return "<ol style=\"padding-left:20px;margin:12px 0;\">" + "".join(itens) + "</ol>"

    def _build_csv_attachment(self, aniversariantes, data_aniversario):
        csv_render = render_to_string(
            "atendimentos/reports/aniversariantes_gestores.csv",
            {"aniversariantes": aniversariantes, "data_aniversario": data_aniversario},
        )
        return "\ufeff" + csv_render

    def _build_pdf_attachment(self, automacao, aniversariantes, data_base, data_aniversario, categoria):
        context = {
            "conta": automacao.conta,
            "categoria": categoria or "TODAS",
            "data_base": data_base,
            "data_aniversario": data_aniversario,
            "total_aniversariantes": len(aniversariantes),
            "aniversariantes": aniversariantes,
            "gerado_em": timezone.localtime(),
        }
        html = render_to_string("atendimentos/reports/aniversariantes_gestores_pdf.html", context)
        return HTML(string=html).write_pdf()

    def _extract_email(self, municipe):
        emails = municipe.emails or []
        if not isinstance(emails, list):
            return None
        for item in emails:
            if isinstance(item, dict):
                value = (item.get("email") or "").strip().lower()
            else:
                value = str(item).strip().lower()
            if value:
                return value
        return None

    def _cargo_perfil_na_conta(self, municipe, conta, categoria_nome=None):
        perfis = (
            PerfilMunicipe.objects.select_related("categoria")
            .filter(municipe=municipe, conta=conta, ativo=True)
            .order_by("-id")
        )
        if categoria_nome:
            perfis = perfis.filter(categoria__nome__iexact=categoria_nome)
        perfil = perfis.first()
        if not perfil:
            return "-"
        return (perfil.cargo or perfil.instituicao or perfil.departamento or "-").strip() or "-"

    def handle(self, *args, **options):
        tables = connection.introspection.table_names()
        required = "atendimentos_automacaoaniversarioconta"
        if required not in tables:
            raise CommandError(
                "Tabela da automação de aniversário não existe. Execute: "
                "'python manage.py migrate atendimentos'."
            )

        data_base = self._parse_data_base(options.get("data_anterior"))
        data_aniversario = data_base + timedelta(days=1)
        conta_id = options.get("conta_id")
        email_teste = (options.get("email_teste") or "").strip().lower() or None
        dry_run = not options.get("send")

        self.stdout.write(self.style.NOTICE("=" * 80))
        self.stdout.write(self.style.NOTICE("Rotina: enviar_alerta_aniversarios_gestores"))
        self.stdout.write(self.style.NOTICE(f"Data base: {data_base.strftime('%d/%m/%Y')}"))
        self.stdout.write(self.style.NOTICE(f"Data aniversário (alvo): {data_aniversario.strftime('%d/%m/%Y')}"))
        if conta_id:
            self.stdout.write(self.style.NOTICE(f"Conta ID: {conta_id}"))
        if email_teste:
            self.stdout.write(self.style.WARNING(f"Modo teste ativo: envio para {email_teste}"))
        self.stdout.write(
            self.style.WARNING("Modo DRY-RUN ativo (sem envio real). Use --send para enviar.")
            if dry_run
            else self.style.SUCCESS("Modo envio REAL ativo (--send).")
        )
        self.stdout.write(self.style.NOTICE("=" * 80))

        automacoes = (
            AutomacaoAniversarioConta.objects.select_related("conta")
            .prefetch_related("alerta_usuarios")
            .filter(ativo=True, alerta_gestores_ativo=True)
            .order_by("conta__nome")
        )
        if conta_id:
            automacoes = automacoes.filter(conta_id=conta_id)

        if not automacoes.exists():
            self.stdout.write(
                self.style.WARNING("Nenhuma automação ativa para alerta de gestores encontrada.")
            )
            return

        total_contas_processadas = 0
        total_envios_ok = 0
        total_envios_falha = 0

        for automacao in automacoes:
            categoria = (automacao.alerta_categoria or "").strip() or None
            aniversariantes_qs = Municipe.objects.filter(
                ativo=True,
                contas=automacao.conta,
                data_nascimento__month=data_aniversario.month,
                data_nascimento__day=data_aniversario.day,
            ).distinct().order_by("nome_completo")
            if categoria:
                aniversariantes_qs = aniversariantes_qs.filter(
                    perfis__conta=automacao.conta,
                    perfis__ativo=True,
                    perfis__categoria__nome__iexact=categoria,
                ).distinct()

            aniversariantes = []
            for municipe in aniversariantes_qs:
                telefone_principal = "-"
                telefones = municipe.telefones or []
                if isinstance(telefones, list) and telefones:
                    primeiro = telefones[0]
                    if isinstance(primeiro, dict):
                        telefone_principal = primeiro.get("numero") or "-"
                    else:
                        telefone_principal = str(primeiro) or "-"
                aniversariantes.append(
                    {
                        "nome_completo": municipe.nome_completo,
                        "telefone_principal": telefone_principal,
                        "categoria": categoria or "TODAS",
                        "cargo": self._cargo_perfil_na_conta(
                            municipe=municipe, conta=automacao.conta, categoria_nome=categoria
                        ),
                    }
                )

            if not aniversariantes:
                self.stdout.write(
                    self.style.WARNING(
                        f"Conta '{automacao.conta.nome}': nenhum aniversariante para {data_aniversario.strftime('%d/%m/%Y')}."
                    )
                )
                continue

            recipients = [u.email.strip().lower() for u in automacao.alerta_usuarios.filter(is_active=True) if u.email]
            if email_teste:
                recipients = [email_teste]

            if not recipients:
                self.stdout.write(
                    self.style.WARNING(
                        f"Conta '{automacao.conta.nome}': sem destinatários de alerta (admin)."
                    )
                )
                continue

            context = {
                "conta_nome": automacao.conta.nome,
                "categoria": categoria or "TODAS",
                "data_base": data_base,
                "data_aniversario": data_aniversario,
                "total_aniversariantes": len(aniversariantes),
            }
            subject, body_txt = self._render_alert_templates(automacao, context)
            if email_teste:
                subject = f"[TESTE] {subject}"

            lista_txt = self._build_lista_aniversariantes_texto(aniversariantes)
            lista_html = self._build_lista_aniversariantes_html(aniversariantes)
            body_html = (
                "<html><body style=\"font-family:Arial,sans-serif;color:#333;\">"
                f"<p>{body_txt.replace(chr(10), '<br>')}</p>"
                f"<p><strong>{len(aniversariantes)} aniversariante(s) em "
                f"{data_aniversario.strftime('%d/%m/%Y')}:</strong></p>"
                f"{lista_html}"
                "<p style=\"font-size:12px;color:#666;\">"
                "Lista acima para copiar e colar em mensagem. Anexos: CSV e PDF.</p>"
                "</body></html>"
            )
            body_plain = (
                f"{body_txt}\n\n"
                f"Aniversariantes em {data_aniversario.strftime('%d/%m/%Y')} "
                f"({len(aniversariantes)}):\n\n"
                f"{lista_txt}\n\n"
                "Anexos: CSV e PDF com o relatório completo."
            )
            attachment_content = self._build_csv_attachment(aniversariantes, data_aniversario)
            pdf_content = self._build_pdf_attachment(
                automacao=automacao,
                aniversariantes=aniversariantes,
                data_base=data_base,
                data_aniversario=data_aniversario,
                categoria=categoria,
            )
            attachment_name = (
                f"aniversariantes_{automacao.conta.nome_sigla or automacao.conta_id}_"
                f"{data_aniversario.strftime('%Y%m%d')}.csv"
            )
            pdf_name = (
                f"aniversariantes_{automacao.conta.nome_sigla or automacao.conta_id}_"
                f"{data_aniversario.strftime('%Y%m%d')}.pdf"
            )

            if dry_run:
                total_contas_processadas += 1
                total_envios_ok += 1
                self.stdout.write(
                    f"[DRY-RUN] conta={automacao.conta.nome} categoria={(categoria or 'TODAS')} destinatarios={len(recipients)} "
                    f"aniversariantes={len(aniversariantes)} assunto='{subject}' anexos='{attachment_name}', '{pdf_name}'"
                )
                continue

            smtp_conn = None
            try:
                smtp_conn, from_email = self._open_smtp_connection(automacao)
                msg = EmailMultiAlternatives(
                    subject=subject,
                    body=body_plain,
                    from_email=from_email,
                    to=recipients,
                    connection=smtp_conn,
                )
                msg.attach_alternative(body_html, "text/html")
                msg.attach(attachment_name, attachment_content, "text/csv; charset=utf-8")
                msg.attach(pdf_name, pdf_content, "application/pdf")
                msg.send()
                total_contas_processadas += 1
                total_envios_ok += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[OK] conta={automacao.conta.nome} categoria={(categoria or 'TODAS')} destinatarios={len(recipients)} "
                        f"aniversariantes={len(aniversariantes)}"
                    )
                )
            except Exception as exc:
                total_contas_processadas += 1
                total_envios_falha += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"[FALHA] conta={automacao.conta.nome} erro={exc}"
                    )
                )
            finally:
                if smtp_conn:
                    try:
                        smtp_conn.close()
                    except Exception:
                        pass

        self.stdout.write(self.style.NOTICE("-" * 80))
        self.stdout.write(
            self.style.SUCCESS(
                f"Resumo: contas_processadas={total_contas_processadas} "
                f"envios_ok={total_envios_ok} envios_falha={total_envios_falha} dry_run={dry_run}"
            )
        )
