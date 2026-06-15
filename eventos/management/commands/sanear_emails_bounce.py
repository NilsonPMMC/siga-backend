import re
from django.core.management.base import BaseCommand
from django.db.models import Q
from eventos.models import LogDeEnvio, EmailSupressao


class Command(BaseCommand):
    help = (
        "Varre logs de envio com falha e marca e-mails com bounce/invalid para supressão. "
        "Roda em DRY-RUN por padrão (use --apply para efetivar)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Executa supressão real (cria/atualiza registros). Sem essa flag, roda em DRY-RUN.',
        )
        parser.add_argument(
            '--limite',
            type=int,
            default=0,
            help='Limita quantidade de logs processados (0 = sem limite).',
        )
        parser.add_argument(
            '--dias',
            type=int,
            default=0,
            help='Processa apenas logs dos últimos N dias (0 = todos).',
        )

    # Padrões regex para detectar bounce e invalid address
    BOUNCE_PATTERNS = [
        r"invalid\s+address",
        r"user\s+unknown",
        r"mailbox\s+(?:not\s+)?(?:found|unavailable)",
        r"recipient\s+(?:address\s+)?rejected",
        r"does\s+not\s+exist",
        r"no\s+such\s+(?:user|mailbox)",
        r"undeliverable",
        r"550\s+5\.1\.1",  # Código SMTP para user unknown
        r"550\s+5\.7\.1",  # Código SMTP para access denied
        r"554\s+5\.7\.1",  # Mailbox not found
        r"recipient\s+address\s+must\s+contain\s+a\s+domain",  # Endereço sem domínio
        r"malformed\s+address",  # Endereço malformado
        r"Invalid\s+address",  # Endereço inválido (caso Django)
    ]

    def _detectar_bounce(self, detalhe_erro):
        """
        Verifica se o detalhe de erro indica bounce/invalid.
        Retorna tupla: (bool, motivo_str)
        """
        if not detalhe_erro:
            return (False, None)
        
        texto = detalhe_erro.lower()
        for pattern in self.BOUNCE_PATTERNS:
            if re.search(pattern, texto, re.IGNORECASE):
                return (True, 'bounce')
        
        # Detecta sintaxe inválida
        if 'invalid' in texto and 'syntax' in texto:
            return (True, 'invalid_syntax')
        
        return (False, None)

    def _extrair_email_do_log(self, log):
        """
        Tenta extrair e-mail do destinatário associado ao log.
        """
        try:
            emails = log.destinatario.municipe.emails or []
            if not isinstance(emails, list):
                return None
            for item in emails:
                if isinstance(item, dict) and item.get('email'):
                    return item['email'].strip().lower()
                elif isinstance(item, str):
                    return item.strip().lower()
        except Exception:
            pass
        return None

    def handle(self, *args, **options):
        apply_mode = options['apply']
        limite = options['limite']
        dias = options['dias']

        self.stdout.write(self.style.NOTICE('=' * 80))
        self.stdout.write(self.style.NOTICE('Rotina: sanear_emails_bounce'))
        self.stdout.write(
            self.style.SUCCESS('Modo APPLY ativo (supressão real).')
            if apply_mode
            else self.style.WARNING('Modo DRY-RUN ativo (sem criar/atualizar supressões). Use --apply para efetivar.')
        )
        if limite:
            self.stdout.write(self.style.NOTICE(f'Limite: {limite} log(s)'))
        if dias:
            self.stdout.write(self.style.NOTICE(f'Processando logs dos últimos {dias} dia(s)'))
        self.stdout.write(self.style.NOTICE('=' * 80))

        # Busca logs com falha
        logs_qs = LogDeEnvio.objects.filter(status='falha').select_related(
            'destinatario',
            'destinatario__municipe'
        ).order_by('-data_envio')

        if dias > 0:
            from django.utils import timezone
            from datetime import timedelta
            data_limite = timezone.now() - timedelta(days=dias)
            logs_qs = logs_qs.filter(data_envio__gte=data_limite)

        if limite > 0:
            logs_qs = logs_qs[:limite]

        total_logs = logs_qs.count()
        self.stdout.write(self.style.NOTICE(f'Total de logs com falha: {total_logs}'))

        processados = 0
        bounce_detectados = 0
        criados = 0
        atualizados = 0
        ignorados = 0

        for log in logs_qs.iterator(chunk_size=500):
            processados += 1

            # Detecta se é bounce
            eh_bounce, motivo = self._detectar_bounce(log.detalhe_erro)
            if not eh_bounce:
                ignorados += 1
                continue

            # Extrai e-mail
            email = self._extrair_email_do_log(log)
            if not email:
                self.stdout.write(
                    self.style.WARNING(
                        f'[AVISO] Log {log.id}: bounce detectado, mas não foi possível extrair e-mail.'
                    )
                )
                ignorados += 1
                continue

            bounce_detectados += 1

            if not apply_mode:
                self.stdout.write(
                    f'[DRY-RUN] email={email} motivo={motivo} log_id={log.id} erro="{log.detalhe_erro[:80]}..."'
                )
                continue

            # Cria ou atualiza supressão
            supressao, created = EmailSupressao.objects.get_or_create(
                email=email,
                defaults={
                    'status': 'ativo',
                    'motivo': motivo,
                    'origem': 'log_envio',
                    'ocorrencias': 1,
                    'observacao': f'Detectado automaticamente via log {log.id}',
                }
            )

            if created:
                criados += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[CRIADO] email={email} motivo={motivo} log_id={log.id}'
                    )
                )
            else:
                # Incrementa ocorrências se já existia
                if supressao.status == 'ativo':
                    supressao.incrementar_ocorrencia()
                    atualizados += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'[ATUALIZADO] email={email} ocorrencias={supressao.ocorrencias} log_id={log.id}'
                        )
                    )
                else:
                    # Não atualiza se foi liberado manualmente
                    self.stdout.write(
                        self.style.NOTICE(
                            f'[IGNORADO] email={email} (status={supressao.status}) log_id={log.id}'
                        )
                    )

        self.stdout.write(self.style.NOTICE('-' * 80))
        self.stdout.write(
            self.style.SUCCESS(
                f'Resumo: logs_processados={processados} bounce_detectados={bounce_detectados} '
                f'criados={criados} atualizados={atualizados} ignorados={ignorados} '
                f'dry_run={not apply_mode}'
            )
        )
