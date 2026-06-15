"""
Simulação de unificação de munícipes (dry-run) — mesma regra de negócio de UnificarMunicipesView,
sem persistir alterações.
"""
from __future__ import annotations

from typing import Any

from atendimentos.models import Municipe, PerfilMunicipe


def _resumo_municipe(municipe: Municipe) -> dict[str, Any]:
    return {
        'id': municipe.id,
        'nome_completo': municipe.nome_completo,
        'cpf': municipe.cpf or None,
        'email': (municipe.emails or [{}])[0].get('email') if municipe.emails else None,
    }


def _preview_dados_herdados(principal: Municipe, duplicado: Municipe) -> dict[str, Any]:
    telefones_adicionados = []
    if duplicado.telefones:
        numeros_existentes = [t.get('numero') for t in (principal.telefones or []) if t.get('numero')]
        for tel in duplicado.telefones:
            numero = tel.get('numero')
            if numero and numero not in numeros_existentes:
                telefones_adicionados.append(tel)

    emails_adicionados = []
    if duplicado.emails:
        emails_existentes = [e.get('email') for e in (principal.emails or []) if e.get('email')]
        for mail in duplicado.emails:
            email = mail.get('email')
            if email and email not in emails_existentes:
                emails_adicionados.append(mail)

    return {
        'cpf': duplicado.cpf if not principal.cpf and duplicado.cpf else None,
        'matricula_rh': duplicado.matricula_rh if not principal.matricula_rh and duplicado.matricula_rh else None,
        'foto': bool(not principal.foto and duplicado.foto),
        'telefones_adicionados': telefones_adicionados,
        'emails_adicionados': emails_adicionados,
    }


def _preview_vinculos_one_to_many(principal: Municipe, duplicado: Municipe) -> tuple[list[dict], list[dict], int]:
    transferir: list[dict] = []
    descartar: list[dict] = []
    links = 0

    for rel in Municipe._meta.get_fields():
        if not (rel.one_to_many and rel.auto_created):
            continue

        related_model = rel.related_model
        remote_field_name = rel.field.name
        verbose = related_model._meta.verbose_name_plural.title()

        if related_model.__name__ == 'PerfilMunicipe':
            perfis_dup = list(related_model.objects.filter(**{remote_field_name: duplicado}))
            perfis_transferir = []
            perfis_descartar = []
            for perfil in perfis_dup:
                conflito = related_model.objects.filter(
                    municipe=principal,
                    conta=perfil.conta,
                    cargo=perfil.cargo,
                ).exists()
                item = {
                    'id': perfil.id,
                    'conta_id': perfil.conta_id,
                    'conta_nome': perfil.conta.nome if perfil.conta_id else None,
                    'cargo': perfil.cargo,
                    'categoria': perfil.categoria.nome if perfil.categoria_id else None,
                }
                if conflito:
                    perfis_descartar.append(item)
                else:
                    perfis_transferir.append(item)
                    links += 1

            if perfis_transferir:
                transferir.append({
                    'modelo': 'PerfilMunicipe',
                    'nome': verbose,
                    'quantidade': len(perfis_transferir),
                    'itens': perfis_transferir,
                })
            if perfis_descartar:
                descartar.append({
                    'modelo': 'PerfilMunicipe',
                    'nome': verbose,
                    'quantidade': len(perfis_descartar),
                    'motivo': 'Principal já possui perfil com mesmo cargo e conta',
                    'itens': perfis_descartar,
                })
            continue

        try:
            count = related_model.objects.filter(**{remote_field_name: duplicado}).count()
        except Exception:
            continue

        if count:
            transferir.append({
                'modelo': related_model.__name__,
                'nome': verbose,
                'quantidade': count,
            })
            links += count

    return transferir, descartar, links


def _preview_vinculos_m2m(principal: Municipe, duplicado: Municipe) -> tuple[list[dict], int]:
    transferir: list[dict] = []
    links = 0

    for rel in Municipe._meta.get_fields():
        if not (rel.many_to_many and rel.auto_created):
            continue

        related_model = rel.related_model
        remote_field_name = rel.field.name
        verbose = related_model._meta.verbose_name_plural.title()

        try:
            count = related_model.objects.filter(**{remote_field_name: duplicado}).count()
        except Exception:
            continue

        if count:
            transferir.append({
                'modelo': related_model.__name__,
                'nome': verbose,
                'quantidade': count,
            })
            links += count

    return transferir, links


def preview_unificacao_municipes(principal: Municipe, duplicado: Municipe) -> dict[str, Any]:
    """Retorna simulação da fusão sem gravar no banco."""
    bloqueios: list[str] = []
    avisos: list[str] = []

    if principal.pk == duplicado.pk:
        bloqueios.append('Os dois registros são o mesmo munícipe.')

    transferir_o2m, descartar_o2m, links_o2m = _preview_vinculos_one_to_many(principal, duplicado)
    transferir_m2m, links_m2m = _preview_vinculos_m2m(principal, duplicado)

    for grupo in descartar_o2m:
        avisos.append(
            f"{grupo['quantidade']} perfil(is) será(ão) descartado(s): "
            f"{grupo.get('motivo', 'conflito de unicidade')}."
        )

    dados_herdados = _preview_dados_herdados(principal, duplicado)
    links_migrados = links_o2m + links_m2m

    return {
        'bloqueios': bloqueios,
        'avisos': avisos,
        'pode_unificar': len(bloqueios) == 0,
        'principal': _resumo_municipe(principal),
        'duplicado': _resumo_municipe(duplicado),
        'dados_herdados': dados_herdados,
        'vinculos_transferir': transferir_o2m + transferir_m2m,
        'vinculos_descartar': descartar_o2m,
        'links_migrados': links_migrados,
        'duplicado_sera_excluido': True,
    }
