"""
Comando de gestão: Processa atendimentos que precisam de resumo e vetor IA.
Varre atendimentos com auditoria_ia_status PENDENTE ou ERRO, gera resumo (DeepSeek)
e embedding (mxbai-embed-large), marca como PROCESSADO.
"""
import time

from django.core.management.base import BaseCommand
from django.db import transaction

from atendimentos.models import Atendimento
from atendimentos.services.ia_intelligence import gerar_resumo_atendimento, atualizar_vetor_atendimento


class Command(BaseCommand):
    help = "Processa atendimentos PENDENTE/ERRO: gera resumo IA e vetor, marca como PROCESSADO."

    def add_arguments(self, parser):
        parser.add_argument(
            '--limite',
            type=int,
            default=50,
            help='Máximo de atendimentos a processar por execução (default: 50)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Apenas lista os atendimentos que seriam processados, sem alterar',
        )

    def handle(self, *args, **options):
        limite = options['limite']
        dry_run = options['dry_run']

        qs = (
            Atendimento.objects
            .filter(auditoria_ia_status__in=('PENDENTE', 'ERRO'))
            .order_by('id')[:limite]
        )
        atendimentos = list(qs)
        total = len(atendimentos)

        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nenhum atendimento PENDENTE ou ERRO para processar.'))
            return

        if dry_run:
            self.stdout.write(f'[DRY-RUN] Seriam processados {total} atendimento(s):')
            for a in atendimentos:
                self.stdout.write(f'  - {a.protocolo} (id={a.id}, status={a.auditoria_ia_status})')
            return

        self.stdout.write(f'Processando {total} atendimento(s) com status PENDENTE ou ERRO...\n')

        ok = 0
        erros = 0

        for i, atendimento in enumerate(atendimentos, start=1):
            self.stdout.write(f'Processando {i}/{total}: {atendimento.protocolo}...', ending=' ')
            try:
                with transaction.atomic():
                    resumo = gerar_resumo_atendimento(atendimento)
                    if resumo is None:
                        atendimento.auditoria_ia_status = 'ERRO'
                        atendimento.save(update_fields=['auditoria_ia_status'])
                        erros += 1
                        self.stdout.write(self.style.WARNING('ERRO (resumo não gerado)'))
                        continue

                    atendimento.resumo_ia_local = resumo
                    atendimento.save(update_fields=['resumo_ia_local'])

                    if not atualizar_vetor_atendimento(atendimento):
                        atendimento.auditoria_ia_status = 'ERRO'
                        atendimento.save(update_fields=['auditoria_ia_status'])
                        erros += 1
                        self.stdout.write(self.style.WARNING('ERRO (embedding não gerado)'))
                        continue

                    atendimento.auditoria_ia_status = 'PROCESSADO'
                    atendimento.save(update_fields=['auditoria_ia_status'])
                    ok += 1
                    self.stdout.write(self.style.SUCCESS('OK'))
            except Exception as e:
                erros += 1
                try:
                    atendimento.auditoria_ia_status = 'ERRO'
                    atendimento.save(update_fields=['auditoria_ia_status'])
                except Exception:
                    pass
                self.stdout.write(self.style.ERROR(f'ERRO: {e}'))
            finally:
                time.sleep(2)

        self.stdout.write(self.style.SUCCESS(f'\nConcluído: {ok} processados, {erros} erros.'))
