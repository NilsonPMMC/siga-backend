from django.core.management.base import BaseCommand
from django.utils import timezone

from atendimentos.models import CampanhaEmail
from atendimentos.services.campanhas_email import process_campaign_send


class Command(BaseCommand):
    help = "Processa campanhas de e-mail agendadas/imediatas pendentes para disparo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limita a quantidade de destinatários por campanha (0 = sem limite).",
        )

    def handle(self, *args, **options):
        limit = options.get("limit") or 0
        if limit < 0:
            self.stderr.write(self.style.ERROR("--limit não pode ser negativo."))
            return

        agora = timezone.now()
        campanhas = CampanhaEmail.objects.filter(
            status="AGENDADA",
            disparo_solicitado_em__isnull=False,
            data_hora_disparo__lte=agora,
        ).order_by("data_hora_disparo")

        if not campanhas.exists():
            self.stdout.write("Nenhuma campanha pendente para processamento.")
            return

        self.stdout.write(
            self.style.NOTICE(f"Processando {campanhas.count()} campanha(s) pendente(s)...")
        )

        total_ok = 0
        total_fail = 0
        for campanha in campanhas:
            self.stdout.write(f"Campanha: {campanha.nome} (Conta: {campanha.conta.nome})")
            try:
                result = process_campaign_send(campanha, limit=limit or None)
                self.stdout.write(
                    self.style.SUCCESS(
                        f" -> Enviados: {result['enviados']} | Falhas: {result['falhas']}"
                    )
                )
                total_ok += 1
            except Exception as exc:
                campanha.status = "ERRO"
                campanha.save(update_fields=["status", "data_atualizacao"])
                self.stdout.write(self.style.ERROR(f" -> Erro ao processar campanha: {exc}"))
                total_fail += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processamento concluído. Campanhas OK: {total_ok} | Campanhas com erro: {total_fail}"
            )
        )
