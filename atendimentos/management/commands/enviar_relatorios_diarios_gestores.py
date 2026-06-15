import os
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import connection
from django.template import Context, Template, TemplateSyntaxError
from django.utils import timezone
from django.utils.html import strip_tags

from atendimentos.models import AutomacaoRelatorioDiarioConta
from atendimentos.services.relatorios_pdf_envio import gerar_pdf_atendimentos


class Command(BaseCommand):
    help = (
        "Envia o relatório diário de atendimentos em PDF para usuários internos, "
        "com base nas configurações da Automação de Relatório Diário por Conta."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-referencia",
            dest="data_referencia",
            type=str,
            default=None,
            help="Data de referência dos relatórios no formato YYYY-MM-DD. Padrão: hoje (timezone local).",
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

    def _parse_data_referencia(self, data_referencia):
        if not data_referencia:
            return timezone.localdate()
        try:
            return datetime.strptime(data_referencia, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError(
                f"Formato inválido para --data-referencia: {data_referencia}. Use YYYY-MM-DD."
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

    def _render_templates(self, automacao, context):
        try:
            subject = Template(automacao.assunto_template).render(Context(context)).strip()
            body_txt = Template(automacao.corpo_template).render(Context(context)).strip()
        except TemplateSyntaxError as exc:
            raise CommandError(
                f"Template inválido para conta '{automacao.conta.nome}': {exc}"
            ) from exc
        if not subject:
            subject = (
                f"Relatório diário de atendimentos - {context['conta_nome']} - "
                f"{context['data_referencia'].strftime('%d/%m/%Y')}"
            )
        return subject, body_txt

    def handle(self, *args, **options):
        tables = connection.introspection.table_names()
        required = "atendimentos_automacaorelatoriodiarioconta"
        if required not in tables:
            raise CommandError(
                "Tabela da automação de relatório diário não existe. Execute: "
                "'python manage.py migrate atendimentos'."
            )

        data_execucao = self._parse_data_referencia(options.get("data_referencia"))
        conta_id = options.get("conta_id")
        email_teste = (options.get("email_teste") or "").strip().lower() or None
        dry_run = not options.get("send")

        self.stdout.write(self.style.NOTICE("=" * 80))
        self.stdout.write(self.style.NOTICE("Rotina: enviar_relatorios_diarios_gestores"))
        self.stdout.write(self.style.NOTICE(f"Data base da execução: {data_execucao.strftime('%d/%m/%Y')}"))
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
            AutomacaoRelatorioDiarioConta.objects.select_related("conta")
            .prefetch_related("destinatarios")
            .filter(ativo=True)
            .order_by("conta__nome")
        )
        if conta_id:
            automacoes = automacoes.filter(conta_id=conta_id)

        if not automacoes.exists():
            self.stdout.write(
                self.style.WARNING("Nenhuma automação de relatório diário ativa encontrada.")
            )
            return

        total_contas_processadas = 0
        total_envios_ok = 0
        total_envios_falha = 0

        for automacao in automacoes:
            data_referencia = data_execucao - timedelta(days=automacao.dias_offset or 0)
            if not automacao.enviar_relatorio_atendimentos:
                self.stdout.write(
                    self.style.WARNING(
                        f"Conta '{automacao.conta.nome}': relatório de atendimentos desabilitado."
                    )
                )
                continue

            recipients = [
                u.email.strip().lower()
                for u in automacao.destinatarios.filter(is_active=True)
                if u.email
            ]
            if email_teste:
                recipients = [email_teste]

            if not recipients:
                self.stdout.write(
                    self.style.WARNING(
                        f"Conta '{automacao.conta.nome}': sem destinatários (admin)."
                    )
                )
                continue

            context = {
                "conta_nome": automacao.conta.nome,
                "data_referencia": data_referencia,
                "data_execucao": data_execucao,
            }
            subject, body_txt = self._render_templates(automacao, context)
            if email_teste:
                subject = f"[TESTE] {subject}"

            anexos = []
            sigla = automacao.conta.nome_sigla or str(automacao.conta_id)
            data_tag = data_referencia.strftime("%Y%m%d")
            nome_atend = f"relatorio_atendimentos_{sigla}_{data_tag}.pdf"

            if dry_run:
                anexos.append((nome_atend, b""))
            else:
                try:
                    pdf = gerar_pdf_atendimentos(automacao.conta_id, data_referencia)
                    anexos.append((nome_atend, pdf))
                except Exception as exc:
                    total_contas_processadas += 1
                    total_envios_falha += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"[FALHA] conta={automacao.conta.nome} erro_geracao_atendimentos={exc}"
                        )
                    )
                    continue

            body_html = (
                "<html><body>"
                f"<p>{body_txt.replace(chr(10), '<br>')}</p>"
                "<p><strong>Anexo:</strong> "
                f"{', '.join(nome for nome, _ in anexos)}</p>"
                "</body></html>"
            )

            if dry_run:
                total_contas_processadas += 1
                total_envios_ok += 1
                self.stdout.write(
                    f"[DRY-RUN] conta={automacao.conta.nome} data_ref={data_referencia.strftime('%d/%m/%Y')} "
                    f"destinatarios={len(recipients)} anexos={[n for n, _ in anexos]} assunto='{subject}'"
                )
                continue

            smtp_conn = None
            try:
                smtp_conn, from_email = self._open_smtp_connection(automacao)
                msg = EmailMultiAlternatives(
                    subject=subject,
                    body=strip_tags(body_html),
                    from_email=from_email,
                    to=recipients,
                    connection=smtp_conn,
                )
                msg.attach_alternative(body_html, "text/html")
                for nome, conteudo in anexos:
                    msg.attach(nome, conteudo, "application/pdf")
                msg.send()
                total_contas_processadas += 1
                total_envios_ok += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[OK] conta={automacao.conta.nome} data_ref={data_referencia.strftime('%d/%m/%Y')} "
                        f"destinatarios={len(recipients)} anexos={[n for n, _ in anexos]}"
                    )
                )
            except Exception as exc:
                total_contas_processadas += 1
                total_envios_falha += 1
                self.stdout.write(
                    self.style.ERROR(f"[FALHA] conta={automacao.conta.nome} erro={exc}")
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
