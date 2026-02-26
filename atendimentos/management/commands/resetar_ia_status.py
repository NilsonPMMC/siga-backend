"""
Comando de gestão: Reseta o status da IA em todos os atendimentos.
Força o reprocessamento: define auditoria_ia_status=PENDENTE e vetor_ia_atendimento=None.
Use depois para rodar processar_ia_atendimentos com a nova lógica de vetores.
"""
from django.core.management.base import BaseCommand

from atendimentos.models import Atendimento


class Command(BaseCommand):
    help = "Reseta auditoria_ia_status e vetor_ia_atendimento em todos os atendimentos para reprocessamento."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Apenas exibe quantos seriam resetados, sem alterar',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        total = Atendimento.objects.count()

        if total == 0:
            self.stdout.write(self.style.WARNING('Nenhum atendimento encontrado.'))
            return

        if dry_run:
            self.stdout.write(f'[DRY-RUN] Seriam resetados {total} atendimento(s).')
            return

        qtd = Atendimento.objects.update(
            auditoria_ia_status='PENDENTE',
            vetor_ia_atendimento=None,
        )

        self.stdout.write(self.style.SUCCESS(f'Resetados {qtd} atendimento(s). Execute processar_ia_atendimentos para reprocessar.'))
