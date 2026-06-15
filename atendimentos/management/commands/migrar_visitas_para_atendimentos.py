"""
Migra RegistroVisita → Atendimento (Fase 4).

Exemplos:
  python manage.py migrar_visitas_para_atendimentos --dry-run
  python manage.py migrar_visitas_para_atendimentos --apply
  python manage.py migrar_visitas_para_atendimentos --apply --conta-id 1 --limite 50
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from atendimentos.models import RegistroVisita
from atendimentos.services.visita_atendimento import migrar_registro_visita


class Command(BaseCommand):
    help = "Migra registros de visita (check-in) para atendimentos unificados."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Executa a migração. Sem esta flag, roda em DRY-RUN.',
        )
        parser.add_argument(
            '--conta-id',
            type=int,
            default=None,
            help='Filtra por conta_destino (gabinete).',
        )
        parser.add_argument(
            '--limite',
            type=int,
            default=None,
            help='Máximo de registros a processar.',
        )
        parser.add_argument(
            '--sobrescrever',
            action='store_true',
            help='Recria atendimento mesmo se já houver vínculo (perigoso).',
        )
        parser.add_argument(
            '--todos',
            action='store_true',
            help='Inclui visitas já vinculadas (padrão: só sem atendimento_id).',
        )

    def handle(self, *args, **options):
        dry_run = not options['apply']
        conta_id = options.get('conta_id')
        limite = options.get('limite')
        sobrescrever = options.get('sobrescrever')
        apenas_sem = not options.get('todos')

        qs = RegistroVisita.objects.select_related(
            'municipe', 'conta_destino', 'usuario_destino', 'registrado_por', 'atendimento'
        ).order_by('data_checkin', 'id')

        if conta_id:
            qs = qs.filter(conta_destino_id=conta_id)
        if apenas_sem and not sobrescrever:
            qs = qs.filter(Q(atendimento__isnull=True))

        total = qs.count()
        if limite:
            visitas = list(qs[:limite])
        else:
            visitas = list(qs)

        self.stdout.write(self.style.NOTICE('=' * 72))
        self.stdout.write(self.style.NOTICE('Rotina: migrar_visitas_para_atendimentos'))
        self.stdout.write(
            self.style.WARNING('Modo DRY-RUN (use --apply para gravar).')
            if dry_run
            else self.style.SUCCESS('Modo APPLY — gravando no banco.')
        )
        self.stdout.write(self.style.NOTICE(f'Encontrados: {total} | Neste lote: {len(visitas)}'))
        self.stdout.write(self.style.NOTICE('=' * 72))

        if not visitas:
            self.stdout.write(self.style.SUCCESS('Nenhum registro de visita a migrar.'))
            return

        stats = {'migrado': 0, 'simulado': 0, 'ja_vinculado': 0, 'erro': 0}

        for i, visita in enumerate(visitas, start=1):
            try:
                status, atendimento = migrar_registro_visita(
                    visita,
                    dry_run=dry_run,
                    sobrescrever=sobrescrever,
                )
                stats[status] = stats.get(status, 0) + 1
                extra = f' → {atendimento.protocolo}' if atendimento else ''
                self.stdout.write(
                    f'[{i}/{len(visitas)}] id={visita.id} {visita.municipe.nome_completo[:40]} '
                    f'| {status}{extra}'
                )
            except Exception as exc:
                stats['erro'] += 1
                self.stdout.write(self.style.ERROR(f'[{i}] id={visita.id} ERRO: {exc}'))

        self.stdout.write(self.style.NOTICE('-' * 72))
        self.stdout.write(
            self.style.SUCCESS(
                f'Resumo: simulado={stats.get("simulado", 0)} migrado={stats.get("migrado", 0)} '
                f'ja_vinculado={stats.get("ja_vinculado", 0)} erros={stats.get("erro", 0)} dry_run={dry_run}'
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING('Execute com --apply para persistir.'))
