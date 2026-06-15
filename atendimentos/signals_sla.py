"""Signals SLA — prazos automáticos em atendimentos (Épico 10)."""
import os

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Atendimento, SlaAtendimentoConfig
from .services.sla_atendimento import garantir_prazos_sla, recalcular_prazos_abertos


@receiver(post_save, sender=Atendimento)
def aplicar_sla_ao_atendimento(sender, instance, created, **kwargs):
    if os.environ.get('SILENCE_SIGNALS'):
        return
    if created:
        garantir_prazos_sla(instance, force=True)


@receiver(post_save, sender=SlaAtendimentoConfig)
def recalcular_sla_ao_alterar_config(sender, instance, **kwargs):
    if os.environ.get('SILENCE_SIGNALS'):
        return
    if not instance.ativo:
        return
    recalcular_prazos_abertos(
        conta_id=instance.conta_id,
        assunto_id=instance.assunto_id,
    )
