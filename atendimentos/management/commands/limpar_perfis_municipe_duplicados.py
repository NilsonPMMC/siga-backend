from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Coalesce
from django.db.models.fields import CharField

from atendimentos.models import PerfilMunicipe
from eventos.models import Convidado


def status_rank(status: str) -> int:
    """
    Ordena status para preservar o "melhor" ao mesclar convites.
    presente > confirmado > convidado
    """
    ranking = {"presente": 3, "confirmado": 2, "convidado": 1}
    return ranking.get((status or "").lower(), 0)


class Command(BaseCommand):
    help = "Limpa duplicidades de PerfilMunicipe mesclando vínculos (Convidado) para o perfil permanente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Se informado, aplica a limpeza. Se não, roda em dry-run.",
        )
        parser.add_argument(
            "--max-grupos",
            type=int,
            default=0,
            help="Limita a quantidade de grupos duplicados processados (0 = todos).",
        )

    def _group_duplicate_keys(self):
        """
        Agrupa PerfisMunicipe duplicados por chave lógica, tratando NULL como ''.
        """
        qs = (
            PerfilMunicipe.objects.annotate(
                cargo_k=Coalesce(F("cargo"), Value(""), output_field=CharField()),
                instituicao_k=Coalesce(F("instituicao"), Value(""), output_field=CharField()),
                departamento_k=Coalesce(F("departamento"), Value(""), output_field=CharField()),
                tratamento_k=Coalesce(F("tratamento"), Value(""), output_field=CharField()),
            )
            .values(
                "municipe_id",
                "conta_id",
                "categoria_id",
                "cargo_k",
                "instituicao_k",
                "departamento_k",
                "tratamento_k",
            )
            .annotate(qtd=Count("id"))
            .filter(qtd__gt=1)
            .order_by("-qtd", "municipe_id")
        )
        return qs

    @staticmethod
    def _field_cond_eq_null_or_empty(field_name: str, value: str) -> Q:
        """
        Como a chave lógica trata NULL como '', este predicado faz o mesmo.
        """
        if value == "":
            return Q(**{field_name: ""}) | Q(**{field_name + "__isnull": True})
        return Q(**{field_name: value})

    def handle(self, *args, **options):
        apply = bool(options.get("apply"))
        max_grupos = options.get("max_grupos") or 0

        self.stdout.write(
            self.style.NOTICE(
                f"Iniciando limpeza de PerfilMunicipe duplicados (apply={apply}, max_grupos={max_grupos or 'ALL'})."
            )
        )

        duplicate_keys_qs = self._group_duplicate_keys()
        if max_grupos > 0:
            duplicate_keys_qs = duplicate_keys_qs[:max_grupos]

        total_grupos = duplicate_keys_qs.count()
        if total_grupos == 0:
            self.stdout.write(self.style.SUCCESS("Nenhum grupo duplicado encontrado."))
            return

        self.stdout.write(self.style.WARNING(f"Grupos duplicados encontrados: {total_grupos}"))

        grupos_processados = 0
        perfis_removidos_total = 0
        convites_origem_total = 0
        convites_mesclados_total = 0

        for key in duplicate_keys_qs:
            grupos_processados += 1

            municipe_id = key["municipe_id"]
            conta_id = key["conta_id"]
            categoria_id = key["categoria_id"]
            cargo_k = key["cargo_k"]
            instituicao_k = key["instituicao_k"]
            departamento_k = key["departamento_k"]
            tratamento_k = key["tratamento_k"]

            perfis_qs = PerfilMunicipe.objects.filter(
                municipe_id=municipe_id,
                conta_id=conta_id,
                categoria_id=categoria_id,
            )
            perfis_qs = perfis_qs.filter(self._field_cond_eq_null_or_empty("cargo", cargo_k))
            perfis_qs = perfis_qs.filter(self._field_cond_eq_null_or_empty("instituicao", instituicao_k))
            perfis_qs = perfis_qs.filter(self._field_cond_eq_null_or_empty("departamento", departamento_k))
            perfis_qs = perfis_qs.filter(self._field_cond_eq_null_or_empty("tratamento", tratamento_k))

            perfis_qs = perfis_qs.order_by("-ativo", "-id")
            perfis = list(perfis_qs.only("id", "ativo", "municipe_id", "conta_id", "categoria_id", "cargo", "instituicao", "departamento", "tratamento"))
            if len(perfis) < 2:
                continue

            perfil_destino = perfis[0]
            perfil_destino_id = perfil_destino.id
            ids_origem = [p.id for p in perfis[1:]]

            # Contagens para dry-run
            convites_origem_qs = Convidado.objects.filter(perfil_id__in=ids_origem)
            convites_origem = convites_origem_qs.count()

            dest_eventos = set(
                Convidado.objects.filter(perfil_id=perfil_destino_id).values_list("evento_id", flat=True)
            )
            convites_mesclados = (
                convites_origem_qs.filter(evento_id__in=dest_eventos).count() if dest_eventos else 0
            )

            perfis_removidos_total += len(ids_origem)
            convites_origem_total += convites_origem
            convites_mesclados_total += convites_mesclados

            self.stdout.write(
                f"[Grupo {grupos_processados}/{total_grupos}] municipe={municipe_id} conta={conta_id} categoria={categoria_id} "
                f"destino={perfil_destino_id} origens={len(ids_origem)} conv_origem={convites_origem} conv_mesclados={convites_mesclados}"
            )

            if not apply:
                continue

            with transaction.atomic():
                # Mapeia convites destino por evento_id (e vai sendo atualizado em tempo real)
                dest_municipe_id = perfil_destino.municipe_id
                dest_by_event = {
                    c.evento_id: c
                    for c in Convidado.objects.filter(perfil_id=perfil_destino_id).select_related("evento")
                }

                # Reatribui convites de origem para destino, mesclando quando necessário.
                # Iteramos por conjunto de convites da origem para garantir que no final não exista referência ao perfil origem.
                for convite in Convidado.objects.filter(perfil_id__in=ids_origem).order_by("evento_id", "id"):
                    evento_id = convite.evento_id
                    dest_conv = dest_by_event.get(evento_id)

                    if dest_conv:
                        # Mescla status/data_checkin/ordem no destino
                        if status_rank(convite.status) > status_rank(dest_conv.status):
                            dest_conv.status = convite.status
                        if dest_conv.data_checkin and convite.data_checkin:
                            if convite.data_checkin > dest_conv.data_checkin:
                                dest_conv.data_checkin = convite.data_checkin
                        elif convite.data_checkin and not dest_conv.data_checkin:
                            dest_conv.data_checkin = convite.data_checkin

                        # Ordem: mantém menor (prioridade)
                        if convite.ordem is not None:
                            dest_conv.ordem = min(dest_conv.ordem or 0, convite.ordem or 0)

                        dest_conv.municipe_id = dest_municipe_id
                        dest_conv.save(update_fields=["status", "data_checkin", "ordem", "municipe"])
                        convite.delete()
                    else:
                        convite.perfil_id = perfil_destino_id
                        convite.municipe_id = dest_municipe_id
                        convite.save(update_fields=["perfil", "municipe"])
                        dest_by_event[evento_id] = convite

                remaining = Convidado.objects.filter(perfil_id__in=ids_origem).count()
                if remaining != 0:
                    raise RuntimeError(
                        f"Falha ao remapear vínculos: ainda existem {remaining} convites referenciando perfis de origem."
                    )

                PerfilMunicipe.objects.filter(id__in=ids_origem).delete()

        self.stdout.write(self.style.SUCCESS("Processamento concluído."))
        self.stdout.write(
            f"Grupos processados={grupos_processados} perfis_removidos_total={perfis_removidos_total} "
            f"convites_origem_total={convites_origem_total} convites_mesclados_total={convites_mesclados_total} "
            f"(apply={apply})"
        )

        if apply:
            after = self._group_duplicate_keys().count()
            self.stdout.write(self.style.SUCCESS(f"Após limpeza, grupos duplicados restantes: {after}"))

