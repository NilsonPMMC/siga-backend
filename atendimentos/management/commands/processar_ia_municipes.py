"""
Comando de gestão: Processa munícipes gerando vetor IA para busca semântica.
Varre munícipes sem vetor ou com auditoria_ia_data anterior à data_atualizacao.
"""
import time

from django.core.management.base import BaseCommand
from django.db.models import F, Q

from atendimentos.models import Municipe
from atendimentos.services.ia_intelligence import atualizar_vetor_municipe


class Command(BaseCommand):
    help = "Processa munícipes: gera vetor IA (embedding) para busca semântica."

    def add_arguments(self, parser):
        parser.add_argument(
            '--limite',
            type=int,
            default=50,
            help='Máximo de munícipes a processar por execução (default: 50)',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Força reprocessamento de todos (ignora filtro de vetor/auditoria)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Apenas lista os munícipes que seriam processados, sem alterar',
        )

    def handle(self, *args, **options):
        limite = options['limite']
        reset = options['reset']
        dry_run = options['dry_run']

        if reset:
            qs = Municipe.objects.all().order_by('id')[:limite]
        else:
            qs = (
                Municipe.objects
                .filter(
                    Q(vetor_ia_perfil__isnull=True)
                    | Q(auditoria_ia_data__isnull=True)
                    | Q(auditoria_ia_data__lt=F('data_atualizacao'))
                )
                .order_by('id')[:limite]
            )

        municipes = list(qs)
        total = len(municipes)

        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nenhum munícipe para processar.'))
            return

        if dry_run:
            self.stdout.write(f'[DRY-RUN] Seriam processados {total} munícipe(s):')
            for m in municipes:
                self.stdout.write(f'  - {m.nome_completo} (id={m.id})')
            return

        self.stdout.write(f'Processando {total} munícipe(s)...\n')

        ok = 0
        erros = 0

        for i, municipe in enumerate(municipes, start=1):
            self.stdout.write(f'Processando {i}/{total}: {municipe.nome_completo} (id={municipe.id})...', ending=' ')
            try:
                if atualizar_vetor_municipe(municipe):
                    ok += 1
                    self.stdout.write(self.style.SUCCESS('OK'))
                else:
                    erros += 1
                    self.stdout.write(self.style.WARNING('ERRO (embedding não gerado)'))
            except Exception as e:
                erros += 1
                self.stdout.write(self.style.ERROR(f'ERRO: {e}'))
            finally:
                time.sleep(1)

        self.stdout.write(self.style.SUCCESS(f'\nConcluído: {ok} processados, {erros} erros.'))
