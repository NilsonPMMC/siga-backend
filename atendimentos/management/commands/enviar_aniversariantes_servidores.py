import os
from collections import defaultdict
from datetime import datetime
from email.mime.image import MIMEImage

from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import connection
from django.template import Context, Template, TemplateSyntaxError
from django.utils import timezone
from django.utils.html import strip_tags

from atendimentos.models import AutomacaoAniversarioConta, PerfilMunicipe


class Command(BaseCommand):
    help = (
        "Envia mensagem de aniversário para munícipes aniversariantes por conta. "
        "Por segurança, roda em DRY-RUN por padrão."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--data",
            dest="data_alvo",
            type=str,
            default=None,
            help="Data alvo no formato YYYY-MM-DD. Padrão: hoje.",
        )
        parser.add_argument(
            "--categoria",
            dest="categoria",
            type=str,
            default=None,
            help=(
                "Categoria exata (case-insensitive). "
                "Se omitido, considera o mailing completo vinculado à conta."
            ),
        )
        parser.add_argument(
            "--conta-id",
            dest="conta_id",
            type=int,
            default=None,
            help="Filtra por uma conta específica.",
        )
        parser.add_argument(
            "--email-teste",
            dest="email_teste",
            type=str,
            default=None,
            help=(
                "Força envio de todas as mensagens para este e-mail (assunto com prefixo [TESTE]). "
                "Útil em homologação."
            ),
        )
        parser.add_argument(
            "--limit",
            dest="limit",
            type=int,
            default=None,
            help="Limita a quantidade de envios processados.",
        )
        parser.add_argument(
            "--send",
            action="store_true",
            dest="send",
            help="Executa envio real. Sem esta flag, roda em DRY-RUN.",
        )

    def _parse_data(self, data_str):
        if not data_str:
            return timezone.localdate()
        try:
            return datetime.strptime(data_str, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError(f"Formato inválido para --data: {data_str}. Use YYYY-MM-DD.") from exc

    def _extract_primary_email(self, municipe):
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

    def _render_templates(self, automacao, contexto):
        try:
            assunto = Template(automacao.assunto_template).render(Context(contexto)).strip()
            corpo_txt = Template(automacao.corpo_template).render(Context(contexto)).strip()
        except TemplateSyntaxError as exc:
            raise CommandError(
                f"Template inválido para conta '{automacao.conta.nome}': {exc}"
            ) from exc
        if not assunto:
            assunto = "Feliz aniversário!"
        return assunto, corpo_txt

    def _abrir_conexao_conta(self, automacao):
        smtp_user = os.getenv(automacao.env_var_smtp_user or "")
        smtp_pass = os.getenv(automacao.env_var_smtp_pass or "")
        if not smtp_user or not smtp_pass:
            raise CommandError(
                "Credenciais SMTP ausentes para conta "
                f"'{automacao.conta.nome}'. Verifique variáveis "
                f"{automacao.env_var_smtp_user} / {automacao.env_var_smtp_pass}."
            )
        connection = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=automacao.smtp_host,
            port=automacao.smtp_port,
            username=smtp_user,
            password=smtp_pass,
            use_tls=automacao.smtp_use_tls,
            use_ssl=automacao.smtp_use_ssl,
            fail_silently=False,
        )
        connection.open()
        from_email = automacao.from_email or smtp_user
        return connection, from_email

    def handle(self, *args, **options):
        tabelas = connection.introspection.table_names()
        if "atendimentos_automacaoaniversarioconta" not in tabelas:
            raise CommandError(
                "Tabela da automação de aniversário ainda não existe. "
                "Execute as migrations antes de usar este comando: "
                "'python manage.py migrate atendimentos'."
            )

        data_alvo = self._parse_data(options.get("data_alvo"))
        categoria = (options.get("categoria") or "").strip() or None
        conta_id = options.get("conta_id")
        email_teste = (options.get("email_teste") or "").strip().lower() or None
        limit = options.get("limit")
        dry_run = not options.get("send")

        if email_teste:
            try:
                validate_email(email_teste)
            except ValidationError as exc:
                raise CommandError(f"Email inválido em --email-teste: {email_teste}") from exc

        self.stdout.write(self.style.NOTICE("=" * 80))
        self.stdout.write(self.style.NOTICE("Rotina: enviar_aniversariantes_servidores"))
        self.stdout.write(self.style.NOTICE(f"Data alvo: {data_alvo.strftime('%d/%m/%Y')}"))
        self.stdout.write(
            self.style.NOTICE(f"Categoria: {categoria}")
            if categoria
            else self.style.NOTICE("Categoria: (sem filtro - mailing completo da conta)")
        )
        if conta_id:
            self.stdout.write(self.style.NOTICE(f"Conta ID: {conta_id}"))
        if email_teste:
            self.stdout.write(
                self.style.WARNING(f"Modo teste ativo: todos os envios irão para {email_teste}.")
            )
        self.stdout.write(
            self.style.WARNING("Modo DRY-RUN ativo (sem envio real). Use --send para enviar.")
            if dry_run
            else self.style.SUCCESS("Modo envio REAL ativo (--send).")
        )
        self.stdout.write(self.style.NOTICE("=" * 80))

        perfis = PerfilMunicipe.objects.select_related("municipe", "conta", "categoria").filter(
            ativo=True,
            municipe__ativo=True,
            municipe__data_nascimento__month=data_alvo.month,
            municipe__data_nascimento__day=data_alvo.day,
        )
        if categoria:
            perfis = perfis.filter(categoria__nome__iexact=categoria)
        perfis = perfis.order_by("conta_id", "municipe__nome_completo")

        if conta_id:
            perfis = perfis.filter(conta_id=conta_id)

        if not perfis.exists():
            self.stdout.write(
                self.style.WARNING("Nenhum aniversariante encontrado para os filtros informados.")
            )
            return

        # Evita envio duplicado para o mesmo munícipe na mesma conta.
        dedup = set()
        elegiveis_por_conta = defaultdict(list)
        sem_email_valido = 0

        for perfil in perfis:
            chave = (perfil.conta_id, perfil.municipe_id)
            if chave in dedup:
                continue
            dedup.add(chave)

            email_destino_original = self._extract_primary_email(perfil.municipe)
            if not email_destino_original:
                sem_email_valido += 1
                continue

            elegiveis_por_conta[perfil.conta_id].append((perfil, email_destino_original))

        if limit and limit > 0:
            restante = limit
            limitado = defaultdict(list)
            for conta_key in sorted(elegiveis_por_conta.keys()):
                if restante <= 0:
                    break
                lote = elegiveis_por_conta[conta_key][:restante]
                limitado[conta_key] = lote
                restante -= len(lote)
            elegiveis_por_conta = limitado

        total_elegiveis = sum(len(v) for v in elegiveis_por_conta.values())
        if not total_elegiveis:
            self.stdout.write(
                self.style.WARNING(
                    "Há aniversariantes, mas nenhum com e-mail válido para envio após deduplicação."
                )
            )
            return

        automacoes = {
            a.conta_id: a
            for a in AutomacaoAniversarioConta.objects.select_related("conta").filter(
                conta_id__in=elegiveis_por_conta.keys()
            )
        }

        contas_sem_automacao = [
            conta_id for conta_id in elegiveis_por_conta.keys() if conta_id not in automacoes
        ]
        if contas_sem_automacao:
            self.stdout.write(
                self.style.WARNING(
                    f"Contas sem configuração de automação: {contas_sem_automacao}. "
                    "Esses aniversariantes serão ignorados."
                )
            )

        contas_inativas = [
            conta_id
            for conta_id, automacao in automacoes.items()
            if not automacao.ativo and conta_id in elegiveis_por_conta
        ]
        if contas_inativas:
            self.stdout.write(
                self.style.WARNING(
                    f"Contas com automação inativa: {contas_inativas}. "
                    "Esses aniversariantes serão ignorados."
                )
            )

        enviados = 0
        falhas = 0
        ignorados_sem_config = 0
        ignorados_inativos = 0

        for conta_id, lote in elegiveis_por_conta.items():
            automacao = automacoes.get(conta_id)
            if not automacao:
                ignorados_sem_config += len(lote)
                continue
            if not automacao.ativo:
                ignorados_inativos += len(lote)
                continue

            smtp_connection = None
            from_email = None
            if not dry_run:
                try:
                    smtp_connection, from_email = self._abrir_conexao_conta(automacao)
                    self.stdout.write(
                        self.style.NOTICE(
                            f"Conta '{automacao.conta.nome}': conexão SMTP aberta."
                        )
                    )
                except Exception as exc:
                    falhas += len(lote)
                    self.stdout.write(
                        self.style.ERROR(
                            f"Conta '{automacao.conta.nome}': falha de conexão SMTP. erro={exc}"
                        )
                    )
                    continue

            try:
                for perfil, email_original in lote:
                    municipe = perfil.municipe
                    conta = perfil.conta
                    destino = email_teste or email_original
                    contexto = {
                        "nome_completo": municipe.nome_completo,
                        "conta_nome": conta.nome,
                        "conta_nome_titular": conta.nome_titular or "",
                        "data_alvo": data_alvo,
                        "email_original": email_original,
                        "email_teste": email_teste,
                    }

                    try:
                        assunto, corpo_txt = self._render_templates(automacao, contexto)
                    except CommandError as exc:
                        falhas += 1
                        self.stdout.write(
                            self.style.ERROR(
                                f"[FALHA] {municipe.nome_completo} | conta={conta.nome} | erro={exc}"
                            )
                        )
                        continue

                    if email_teste:
                        assunto = f"[TESTE] {assunto}"

                    corpo_html = (
                        "<html><body>"
                        f"<p>{corpo_txt.replace(chr(10), '<br>')}</p>"
                        "{imagem_html}"
                        "</body></html>"
                    )
                    if automacao.arte:
                        corpo_html = corpo_html.format(
                            imagem_html=(
                                '<p style="margin-top:15px;">'
                                '<img src="cid:arte_aniversario" alt="Arte de aniversário" '
                                'style="max-width:600px; width:100%; height:auto;">'
                                "</p>"
                            )
                        )
                    else:
                        corpo_html = corpo_html.format(imagem_html="")

                    if dry_run:
                        enviados += 1
                        self.stdout.write(
                            f"[DRY-RUN] {municipe.nome_completo} | conta={conta.nome} | "
                            f"destino={destino} | original={email_original} | smtp_var={automacao.env_var_smtp_user}"
                        )
                        continue

                    try:
                        msg = EmailMultiAlternatives(
                            subject=assunto,
                            body=strip_tags(corpo_html),
                            from_email=from_email,
                            to=[destino],
                            connection=smtp_connection,
                        )
                        msg.attach_alternative(corpo_html, "text/html")
                        if automacao.arte:
                            with automacao.arte.open("rb") as image_file:
                                arte_img = MIMEImage(image_file.read())
                            arte_img.add_header("Content-ID", "<arte_aniversario>")
                            arte_img.add_header("Content-Disposition", "inline", filename="arte-aniversario")
                            msg.mixed_subtype = "related"
                            msg.attach(arte_img)
                        msg.send()
                        enviados += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"[OK] {municipe.nome_completo} | conta={conta.nome} | destino={destino}"
                            )
                        )
                    except Exception as exc:
                        falhas += 1
                        self.stdout.write(
                            self.style.ERROR(
                                f"[FALHA] {municipe.nome_completo} | conta={conta.nome} | destino={destino} | erro={exc}"
                            )
                        )
            finally:
                if smtp_connection:
                    try:
                        smtp_connection.close()
                    except Exception:
                        pass

        self.stdout.write(self.style.NOTICE("-" * 80))
        self.stdout.write(
            self.style.SUCCESS(
                f"Resumo: elegiveis={total_elegiveis} enviados={enviados} falhas={falhas} "
                f"sem_email_valido={sem_email_valido} ignorados_sem_config={ignorados_sem_config} "
                f"ignorados_inativos={ignorados_inativos} dry_run={dry_run}"
            )
        )
