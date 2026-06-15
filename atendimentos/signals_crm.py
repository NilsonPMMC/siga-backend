"""Signals de auditoria CRM — munícipes, perfis e categorias (Épico 11)."""
import os

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import CategoriaContato, Municipe, PerfilMunicipe
from .services.log_crm import (
    diff_fields,
    is_crm_log_suppressed,
    pop_snapshot,
    registrar_log_crm,
    resolve_conta_for_municipe,
    resolve_conta_from_request,
    snapshot_instance,
)

MUNICIPE_LOG_FIELDS = [
    'nome_completo', 'nome_de_guerra', 'cpf', 'data_nascimento',
    'emails', 'telefones', 'endereco', 'observacoes', 'matricula_rh',
    'ativo', 'dados_etiqueta', 'foto',
]
MUNICIPE_NOISE_FIELDS = {
    'auditoria_ia', 'vetor_ia_perfil', 'perfil_ia_texto',
    'auditoria_ia_data', 'data_atualizacao', 'grupo_duplicado',
}
PERFIL_LOG_FIELDS = [
    'categoria_id', 'cargo', 'instituicao', 'departamento', 'tratamento', 'ativo', 'conta_id',
]
CATEGORIA_LOG_FIELDS = ['nome', 'ativa']


def _signals_disabled():
    return os.environ.get('SILENCE_SIGNALS') or is_crm_log_suppressed()


@receiver(pre_save, sender=Municipe)
def snapshot_municipe_pre_save(sender, instance, **kwargs):
    if _signals_disabled():
        return
    snapshot_instance('Municipe', instance, MUNICIPE_LOG_FIELDS)


@receiver(post_save, sender=Municipe)
def log_municipe_save(sender, instance, created, **kwargs):
    if _signals_disabled():
        return
    conta = resolve_conta_from_request() or resolve_conta_for_municipe(instance)
    if created:
        registrar_log_crm(
            acao='MUNICIPE_CRIACAO',
            detalhes=f"Munícipe #{instance.pk} criado: {instance.nome_completo}.",
            content_object=instance,
            conta=conta,
            payload={'nome_completo': instance.nome_completo},
        )
        return

    snapshot = pop_snapshot('Municipe', instance.pk)
    changes = diff_fields(snapshot, instance, MUNICIPE_LOG_FIELDS)
    if not changes:
        return
    registrar_log_crm(
        acao='MUNICIPE_EDICAO',
        detalhes=f"Munícipe #{instance.pk} editado: {instance.nome_completo}.",
        content_object=instance,
        conta=conta,
        payload={'alteracoes': changes},
    )


@receiver(post_delete, sender=Municipe)
def log_municipe_delete(sender, instance, **kwargs):
    if _signals_disabled():
        return
    conta = resolve_conta_for_municipe(instance)
    registrar_log_crm(
        acao='MUNICIPE_DELECAO',
        detalhes=f"Munícipe #{instance.pk} excluído: {instance.nome_completo}.",
        conta=conta,
        payload={'nome_completo': instance.nome_completo, 'id': instance.pk},
        object_id=instance.pk,
    )


@receiver(pre_save, sender=PerfilMunicipe)
def snapshot_perfil_pre_save(sender, instance, **kwargs):
    if _signals_disabled():
        return
    snapshot_instance('PerfilMunicipe', instance, PERFIL_LOG_FIELDS)


@receiver(post_save, sender=PerfilMunicipe)
def log_perfil_save(sender, instance, created, **kwargs):
    if _signals_disabled():
        return
    conta = instance.conta
    nome = instance.municipe.nome_completo if instance.municipe_id else '?'
    if created:
        registrar_log_crm(
            acao='PERFIL_CRIACAO',
            detalhes=(
                f"Perfil #{instance.pk} criado para munícipe #{instance.municipe_id} "
                f"({nome}) — categoria {instance.categoria_id}."
            ),
            content_object=instance,
            conta=conta,
            payload={
                'municipe_id': instance.municipe_id,
                'categoria_id': instance.categoria_id,
                'cargo': instance.cargo,
            },
        )
        return

    snapshot = pop_snapshot('PerfilMunicipe', instance.pk)
    changes = diff_fields(snapshot, instance, PERFIL_LOG_FIELDS)
    if not changes:
        return
    registrar_log_crm(
        acao='PERFIL_EDICAO',
        detalhes=f"Perfil #{instance.pk} editado (munícipe #{instance.municipe_id}, {nome}).",
        content_object=instance,
        conta=conta,
        payload={'alteracoes': changes, 'municipe_id': instance.municipe_id},
    )


@receiver(post_delete, sender=PerfilMunicipe)
def log_perfil_delete(sender, instance, **kwargs):
    if _signals_disabled():
        return
    registrar_log_crm(
        acao='PERFIL_DELECAO',
        detalhes=f"Perfil #{instance.pk} excluído (munícipe #{instance.municipe_id}).",
        conta=instance.conta,
        payload={
            'id': instance.pk,
            'municipe_id': instance.municipe_id,
            'categoria_id': instance.categoria_id,
        },
        object_id=instance.pk,
    )


@receiver(pre_save, sender=CategoriaContato)
def snapshot_categoria_pre_save(sender, instance, **kwargs):
    if _signals_disabled():
        return
    snapshot_instance('CategoriaContato', instance, CATEGORIA_LOG_FIELDS)


@receiver(post_save, sender=CategoriaContato)
def log_categoria_save(sender, instance, created, **kwargs):
    if _signals_disabled():
        return
    if created:
        registrar_log_crm(
            acao='CATEGORIA_CRIACAO',
            detalhes=f"Categoria de contato #{instance.pk} criada: {instance.nome}.",
            content_object=instance,
            conta=resolve_conta_from_request(),
            payload={'nome': instance.nome},
        )
        return

    snapshot = pop_snapshot('CategoriaContato', instance.pk)
    changes = diff_fields(snapshot, instance, CATEGORIA_LOG_FIELDS)
    if not changes:
        return

    if changes.get('ativa') and changes['ativa'].get('depois') is False:
        acao = 'CATEGORIA_DESATIVACAO'
        detalhes = f"Categoria de contato #{instance.pk} desativada: {instance.nome}."
    else:
        acao = 'CATEGORIA_EDICAO'
        detalhes = f"Categoria de contato #{instance.pk} editada: {instance.nome}."

    registrar_log_crm(
        acao=acao,
        detalhes=detalhes,
        content_object=instance,
        conta=resolve_conta_from_request(),
        payload={'alteracoes': changes},
    )


@receiver(post_delete, sender=CategoriaContato)
def log_categoria_delete(sender, instance, **kwargs):
    if _signals_disabled():
        return
    registrar_log_crm(
        acao='CATEGORIA_EDICAO',
        detalhes=f"Categoria de contato #{instance.pk} excluída: {instance.nome}.",
        conta=resolve_conta_from_request(),
        payload={'nome': instance.nome, 'id': instance.pk, 'excluida': True},
        object_id=instance.pk,
    )
