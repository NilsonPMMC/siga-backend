"""
Remove ChecklistItem que nunca aparecem em EventoChecklistItemStatus.

A relação com EventoChecklist é sempre via `EventoChecklistItemStatus` (item_mestre + evento_checklist).
Itens sem nenhuma linha de status são "órfãos" — em geral lixo de scanner ou cadastros nunca usados.

Cuidado: um item mestre legítimo criado *depois* do último evento só ganha linhas de status quando um *novo*
evento for criado (signal). Remover órfãos apaga esse cadastro antecipado.

Uso:
  python manage.py remover_checklistitem_orfaos --dry-run
  python manage.py remover_checklistitem_orfaos --apply
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from eventos.models import ChecklistItem, EventoChecklistItemStatus


class Command(BaseCommand):
    help = "Remove itens mestre de checklist sem vínculo em EventoChecklistItemStatus."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Explícito: só relatar (equivale a omitir --apply).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Exclui do banco. Sem esta flag, apenas lista e conta.",
        )

    def handle(self, *args, **options):
        if options["dry_run"] and options["apply"]:
            raise CommandError("Use --apply ou --dry-run, não ambos.")

        total = ChecklistItem.objects.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("Nenhum ChecklistItem no banco."))
            return

        used_ids = set(
            EventoChecklistItemStatus.objects.values_list("item_mestre_id", flat=True).distinct()
        )
        orphans = ChecklistItem.objects.exclude(pk__in=used_ids)
        n_orf = orphans.count()
        n_keep = total - n_orf

        self.stdout.write(
            self.style.WARNING(
                f"Total ChecklistItem: {total} | com uso em checklist de evento: {len(used_ids)} | órfãos: {n_orf}"
            )
        )

        if n_orf == total:
            raise CommandError(
                "Todos os itens seriam removidos (nenhum item_mestre em EventoChecklistItemStatus). "
                "Abortando por segurança."
            )

        for row in orphans.order_by("pk")[:40]:
            prev = (row.nome or "")[:100].replace("\n", " ")
            self.stdout.write(f"  remover id={row.pk} nome={prev!r}...")
        if n_orf > 40:
            self.stdout.write(f"  ... e mais {n_orf - 40} registro(s).")

        if not options["apply"]:
            self.stdout.write(
                self.style.NOTICE(
                    f"DRY-RUN: seriam excluídos {n_orf} órfão(s); permaneceriam {n_keep} item(ns) em uso. "
                    "Use --apply para executar."
                )
            )
            return

        with transaction.atomic():
            deleted, _ = orphans.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Excluídos {deleted} objeto(s) no agregado Django (inclui ChecklistItem); "
                f"permanecem {n_keep} ChecklistItem vinculados a eventos."
            )
        )
