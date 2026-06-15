"""
Substitui nomes de itens mestre (ChecklistItem) que contenham padrões de ataque conhecidos.

Preserva o registro (FK em EventoChecklistItemStatus usa PROTECT): apenas o campo `nome` é alterado.

Uso:
  python manage.py sanear_nomes_checklistitem --dry-run
  python manage.py sanear_nomes_checklistitem --apply
"""

from django.core.management.base import BaseCommand, CommandError

from eventos.models import ChecklistItem
from eventos.checklist_security import observacoes_parece_ataque


def _nome_substituto(pk: int) -> str:
    base = f"Item inválido removido (ID {pk})"
    return base[:255]


class Command(BaseCommand):
    help = "Substitui campo nome em ChecklistItem com padrões maliciosos por texto seguro."

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

        qs = ChecklistItem.objects.exclude(nome__exact="")
        afetados = []
        for row in qs.iterator(chunk_size=500):
            if observacoes_parece_ataque(row.nome or ""):
                afetados.append(row)

        self.stdout.write(self.style.WARNING(f"Itens mestre com nome suspeito: {len(afetados)}"))
        for row in afetados[:50]:
            prev = (row.nome or "")[:120].replace("\n", " ")
            self.stdout.write(f"  id={row.pk} trecho={prev!r}...")
        if len(afetados) > 50:
            self.stdout.write(f"  ... e mais {len(afetados) - 50} registro(s).")

        if not apply_mode:
            self.stdout.write(
                self.style.NOTICE(
                    "Modo DRY-RUN. Use --apply para substituir `nome` nestes registros."
                )
            )
            return

        n = 0
        for row in afetados:
            novo = _nome_substituto(row.pk)
            ChecklistItem.objects.filter(pk=row.pk).update(nome=novo)
            n += 1
        self.stdout.write(self.style.SUCCESS(f"Atualizados: {n} registro(s)."))
