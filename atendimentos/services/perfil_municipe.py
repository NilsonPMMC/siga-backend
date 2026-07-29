"""Regras de negócio para perfis de munícipe (cargo + conta)."""

from __future__ import annotations

from django.db.models import Q
from rest_framework import serializers

from ..models import PerfilMunicipe


def normalizar_cargo(cargo) -> str:
    return (cargo or "").strip().upper()


def parse_categoria_ids_from_request(request):
    """IDs de categoria na query (inclui categoria_contato_id e categoria_id[] do axios)."""
    if hasattr(request, 'query_params'):
        qp = request.query_params
    else:
        qp = request.GET

    ids = []
    for key in ('categoria_contato_id', 'categoria', 'categoria_id', 'categoria_id[]'):
        if not hasattr(qp, 'getlist'):
            break
        for valor in qp.getlist(key):
            try:
                ids.append(int(valor))
            except (TypeError, ValueError):
                continue
        if ids:
            break
    return ids


def contas_ids_escopo_usuario(user):
    """Contas visíveis ao usuário; None = superuser (sem restrição)."""
    if not user or getattr(user, 'is_superuser', False):
        return None
    if hasattr(user, 'perfil'):
        return set(user.perfil.contas.values_list('id', flat=True))
    return set()


def filtrar_municipes_por_categoria_perfis(queryset, categoria_ids, contas_ids=None):
    """
    Munícipes com PerfilMunicipe ativo na categoria informada.
    Se contas_ids, exige perfil em uma dessas contas (regra épico 1 / escopo).
    """
    if not categoria_ids:
        return queryset
    filtro = Q(
        perfis__categoria_id__in=categoria_ids,
        perfis__ativo=True,
    )
    if contas_ids is not None:
        filtro &= Q(perfis__conta_id__in=contas_ids)
    return queryset.filter(filtro).distinct()


def perfis_para_exibicao(municipe, *, categoria_ids=None, contas_ids=None, cargos_filtro=None):
    """Perfis ativos relevantes para listagem/relatório conforme filtros aplicados."""
    perfis = list(
        municipe.perfis.filter(ativo=True).select_related('categoria', 'conta')
    )
    if contas_ids is not None:
        perfis = [p for p in perfis if p.conta_id in contas_ids]
    if categoria_ids:
        cat_set = {int(c) for c in categoria_ids}
        perfis = [p for p in perfis if p.categoria_id in cat_set]
    if cargos_filtro:
        cargos = {normalizar_cargo(c) for c in cargos_filtro if normalizar_cargo(c)}
        perfis = [p for p in perfis if normalizar_cargo(p.cargo) in cargos]
    return perfis


def categorias_nomes_de_perfis(perfis):
    nomes = set()
    for perfil in perfis:
        if perfil.categoria:
            nomes.add(perfil.categoria.nome)
    return sorted(nomes)


def linhas_cargo_orgao_de_perfis(perfis):
    """Texto 'cargo @ órgão' para colunas de relatório."""
    linhas = []
    for perfil in perfis:
        cargo = (perfil.cargo or '').strip()
        orgao = (perfil.instituicao or perfil.departamento or '').strip()
        if cargo and orgao:
            linhas.append(f'{cargo} @ {orgao}')
        elif cargo or orgao:
            linhas.append(cargo or orgao)
    return linhas


def campos_endereco_municipe(municipe) -> dict[str, str]:
    """Extrai logradouro, número, complemento, bairro, cidade e CEP do JSON de endereço."""
    endereco = getattr(municipe, 'endereco', None) or {}
    vazio = {
        'logradouro': '',
        'numero': '',
        'complemento': '',
        'bairro': '',
        'cidade': '',
        'cep': '',
    }

    if isinstance(endereco, str):
        return {**vazio, 'logradouro': endereco.strip()}
    if not isinstance(endereco, dict):
        return vazio

    campos_estruturados = (
        'logradouro',
        'numero',
        'complemento',
        'comp',
        'bairro',
        'bairro_nome',
        'cidade',
        'cep',
    )
    if not any(str(endereco.get(campo) or '').strip() for campo in campos_estruturados):
        texto_livre = str(endereco.get('texto_livre') or '').strip()
        if texto_livre:
            return {**vazio, 'logradouro': texto_livre}
        return vazio

    return {
        'logradouro': str(endereco.get('logradouro') or endereco.get('rua') or '').strip(),
        'numero': str(endereco.get('numero') or '').strip(),
        'complemento': str(endereco.get('complemento') or endereco.get('comp') or '').strip(),
        'bairro': str(endereco.get('bairro') or endereco.get('bairro_nome') or '').strip(),
        'cidade': str(endereco.get('cidade') or '').strip(),
        'cep': str(endereco.get('cep') or '').strip(),
    }


def extrair_categoria_id(item) -> int | None:
    if hasattr(item, "categoria_id"):
        return item.categoria_id
    if hasattr(item, "categoria") and item.categoria is not None:
        cat = item.categoria
        return cat.id if hasattr(cat, "id") else int(cat)
    if not isinstance(item, dict):
        return None
    categoria = item.get("categoria") or item.get("categoria_id")
    if categoria is None:
        return None
    if hasattr(categoria, "id"):
        return categoria.id
    try:
        return int(categoria)
    except (TypeError, ValueError):
        return None


def extrair_conta_id(item) -> int | None:
    if hasattr(item, "conta_id"):
        return item.conta_id
    if not isinstance(item, dict):
        return None
    conta = item.get("conta") or item.get("conta_id")
    if conta is None:
        return None
    if hasattr(conta, "id"):
        return conta.id
    return int(conta)


def extrair_cargo(item):
    if hasattr(item, "cargo"):
        return item.cargo
    if isinstance(item, dict):
        return item.get("cargo")
    return None


def chave_perfil_conta_cargo(conta_id, cargo) -> tuple[int, str]:
    return (int(conta_id), normalizar_cargo(cargo))


def municipe_tem_perfis_duplicados(perfis) -> bool:
    vistos: set[tuple[int, str]] = set()
    for perfil in perfis:
        conta_id = extrair_conta_id(perfil)
        if conta_id is None:
            continue
        chave = chave_perfil_conta_cargo(conta_id, extrair_cargo(perfil))
        if chave in vistos:
            return True
        vistos.add(chave)
    return False


def validar_perfis_sem_duplicata(perfis_data, *, municipe=None):
    """
    Impede perfis com mesmo (conta, cargo) no payload e no banco (mesmo munícipe).
    """
    if not perfis_data:
        return

    vistos_payload: set[tuple[int, str]] = set()
    erros: list[str] = []

    for idx, item in enumerate(perfis_data, start=1):
        conta_id = extrair_conta_id(item)
        if not conta_id:
            continue

        chave = chave_perfil_conta_cargo(conta_id, extrair_cargo(item))
        if chave in vistos_payload:
            erros.append(
                f"Perfil #{idx}: já existe outro vínculo com o mesmo cargo e conta neste contato."
            )
            continue
        vistos_payload.add(chave)

        if municipe is None:
            continue

        perfil_id = item.get("id")
        existentes = PerfilMunicipe.objects.filter(municipe=municipe, conta_id=conta_id)
        if perfil_id:
            existentes = existentes.exclude(pk=perfil_id)

        for existente in existentes.only("id", "cargo"):
            if chave_perfil_conta_cargo(existente.conta_id, existente.cargo) == chave:
                erros.append(
                    f"Perfil #{idx}: já existe vínculo com cargo '{extrair_cargo(item) or '(vazio)'}' "
                    f"nesta conta para este munícipe."
                )
                break

    if erros:
        raise serializers.ValidationError({"perfis": erros})
