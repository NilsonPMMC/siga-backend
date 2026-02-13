"""
Comando para ajustar data_criacao de atendimentos (ex.: registros inseridos
com data incorreta). Execute uma vez com a lista abaixo ou use --dry-run para simular.
"""
from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from atendimentos.models import Atendimento


# Lista: (data DD/MM/YYYY, protocolo)
AJUSTES = [
    ("07/11/2025", "2026-00169"),
    ("11/11/2025", "2026-00172"),
    ("11/11/2025", "2026-00179"),
    ("11/11/2025", "2026-00180"),
    ("11/11/2025", "2026-00181"),
    ("14/11/2025", "2026-00182"),
    ("02/12/2025", "2026-00183"),
    ("02/12/2025", "2026-00184"),
    ("02/12/2025", "2026-00186"),
    ("10/12/2025", "2026-00187"),
    ("16/12/2025", "2026-00188"),
    ("22/12/2025", "2026-00189"),
    ("22/12/2025", "2026-00190"),
    ("30/12/2025", "2026-00191"),
    ("06/01/2026", "2026-00192"),
    ("07/01/2026", "2026-00193"),
    ("13/01/2026", "2026-00194"),
    ("13/01/2026", "2026-00195"),
    ("22/01/2026", "2026-00196"),
    ("10/02/2026", "2026-00197"),
    ("11/02/2026", "2026-00198"),
    ("11/02/2026", "2026-00199"),
]


class Command(BaseCommand):
    help = "Ajusta data_criacao de atendimentos por protocolo (lista fixa de correções)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas simula; não grava no banco.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("Modo DRY-RUN: nenhuma alteração será salva."))

        ok = 0
        not_found = []

        for data_str, protocolo in AJUSTES:
            try:
                dt = datetime.strptime(data_str.strip(), "%d/%m/%Y")
            except ValueError:
                self.stdout.write(self.style.ERROR(f"Data inválida: {data_str}"))
                continue

            # 09:00 no timezone local
            dt_local = timezone.make_aware(datetime(dt.year, dt.month, dt.day, 9, 0, 0))

            protocolo_limpo = protocolo.strip()
            qs = Atendimento.objects.filter(protocolo=protocolo_limpo)

            if not qs.exists():
                not_found.append(protocolo_limpo)
                continue

            if not dry_run:
                qs.update(data_criacao=dt_local)

            ok += 1
            self.stdout.write(f"  {data_str} -> {protocolo_limpo} {'(simulado)' if dry_run else '(atualizado)'}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Total atualizados: {ok}"))
        if not_found:
            self.stdout.write(self.style.WARNING(f"Protocolos não encontrados: {', '.join(not_found)}"))
