"""
Remove (zera) observações de itens de checklist que contenham padrões de ataque conhecidos.

Não altera outros campos. Linhas com texto legítimo (sem assinatura de ataque) são mantidas.

Uso:
  python manage.py sanear_observacoes_checklist --dry-run
  python manage.py sanear_observacoes_checklist --apply
"""

from django.core.management.base import BaseCommand, CommandError

from eventos.models import EventoChecklistItemStatus
from eventos.checklist_security import observacoes_parece_ataque


class Command(BaseCommand):
    help = "Zera campo observacoes em EventoChecklistItemStatus com padrões maliciosos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Explícito: só relatar (equivale a omitir --apply).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica alterações no banco. Sem esta flag, apenas lista o que seria alterado.",
        )

    def handle(self, *args, **options):
        if options["dry_run"] and options["apply"]:
            raise CommandError("Use --apply ou --dry-run, não ambos.")
        apply_mode = options["apply"]
        qs = EventoChecklistItemStatus.objects.exclude(
            observacoes__isnull=True
        ).exclude(observacoes__exact="")

        afetados = []
        for row in qs.iterator(chunk_size=500):
            if observacoes_parece_ataque(row.observacoes or ""):
                afetados.append(row)

        self.stdout.write(self.style.WARNING(f"Registros com observações suspeitas: {len(afetados)}"))
        for row in afetados[:50]:
            prev = (row.observacoes or "")[:120].replace("\n", " ")
            self.stdout.write(f"  id={row.pk} checklist={row.evento_checklist_id} trecho={prev!r}...")
        if len(afetados) > 50:
            self.stdout.write(f"  ... e mais {len(afetados) - 50} registro(s).")

        if not apply_mode:
            self.stdout.write(self.style.NOTICE("Modo DRY-RUN. Use --apply para zerar observacoes nestes registros."))
            return

        n = 0
        for row in afetados:
            EventoChecklistItemStatus.objects.filter(pk=row.pk).update(observacoes="")
            n += 1
        self.stdout.write(self.style.SUCCESS(f"Atualizados: {n} registro(s). Observações zeradas."))
