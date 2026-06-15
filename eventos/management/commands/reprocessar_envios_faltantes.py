from django.core.management.base import BaseCommand, CommandError
from django.db.models import OuterRef, Exists

from eventos.models import Comunicacao, Destinatario, LogDeEnvio
from eventos.tasks import enviar_destinatario_comunicacao


class Command(BaseCommand):
    help = (
        "Reprocessa apenas destinatários sem log de envio (sucesso/falha) "
        "de comunicações já marcadas como enviadas."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--comunicacao-id",
            type=int,
            default=None,
            help="ID de uma comunicação específica para reprocessar.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limita quantidade de destinatários faltantes processados por comunicação (0 = sem limite).",
        )
        parser.add_argument(
            "--send",
            action="store_true",
            help="Executa envio real. Sem esta flag, roda em dry-run.",
        )

    def handle(self, *args, **options):
        comunicacao_id = options["comunicacao_id"]
        limit = max(int(options["limit"] or 0), 0)
        send = bool(options["send"])

        comunicacoes_qs = Comunicacao.objects.filter(status="enviado")
        if comunicacao_id:
            comunicacoes_qs = comunicacoes_qs.filter(id=comunicacao_id)

        if not comunicacoes_qs.exists():
            raise CommandError("Nenhuma comunicação enviada encontrada para os filtros informados.")

        total_comunicacoes = 0
        total_faltantes = 0
        total_sucesso = 0
        total_falha = 0

        for comunicacao in comunicacoes_qs.order_by("id"):
            total_comunicacoes += 1

            logs_exists_subquery = LogDeEnvio.objects.filter(
                comunicacao_id=comunicacao.id,
                destinatario_id=OuterRef("pk"),
            )
            faltantes_qs = (
                Destinatario.objects.filter(comunicacao_id=comunicacao.id)
                .annotate(tem_log=Exists(logs_exists_subquery))
                .filter(tem_log=False)
                .select_related("municipe")
                .order_by("id")
            )
            if limit:
                faltantes_qs = faltantes_qs[:limit]

            faltantes = list(faltantes_qs)
            qtd_faltantes = len(faltantes)
            total_faltantes += qtd_faltantes

            self.stdout.write(
                self.style.WARNING(
                    f"[comunicacao={comunicacao.id}] faltantes={qtd_faltantes} modo={'SEND' if send else 'DRY-RUN'}"
                )
            )

            if not send or qtd_faltantes == 0:
                continue

            for destinatario in faltantes:
                status_envio, detalhe = enviar_destinatario_comunicacao(
                    comunicacao=comunicacao,
                    destinatario=destinatario,
                    registrar_log=True,
                )
                if status_envio == "sucesso":
                    total_sucesso += 1
                else:
                    total_falha += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"  - falha destinatario={destinatario.id} municipe={destinatario.municipe_id} erro={detalhe}"
                        )
                    )

        self.stdout.write(
            self.style.SUCCESS(
                "Resumo: "
                f"comunicacoes={total_comunicacoes} "
                f"faltantes={total_faltantes} "
                f"sucesso={total_sucesso} "
                f"falha={total_falha} "
                f"modo={'SEND' if send else 'DRY-RUN'}"
            )
        )
