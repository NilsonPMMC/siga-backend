"""Escopo de acesso do perfil Operador CRM (contatos por categoria)."""

from __future__ import annotations

from ..models import Municipe
from ..permissions import is_in_group
from .perfil_municipe import filtrar_municipes_por_categoria_perfis

GRUPO_OPERADOR_CRM = "Operador CRM"

GRUPOS_ACESSO_CONTATOS = [
    "Recepção",
    "Membro do Gabinete",
    "Secretária",
    GRUPO_OPERADOR_CRM,
]

GRUPOS_CONTATOS_AVANCADO = [
    "Recepção",
    "Membro do Gabinete",
    "Secretária",
]


def is_operador_crm(user) -> bool:
    return is_in_group(user, GRUPO_OPERADOR_CRM)


def usuario_tem_acesso_contatos(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return is_in_group(user, GRUPOS_ACESSO_CONTATOS)


def categorias_escopo_usuario(user):
    """
    IDs de categorias permitidas ao usuário.
    None = sem restrição por categoria (demais perfis de contatos).
    set() = operador sem categorias configuradas (nenhum contato visível).
    """
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    if user.is_superuser or not is_operador_crm(user):
        return None
    if not hasattr(user, "perfil"):
        return set()
    return set(user.perfil.categorias_contato.values_list("id", flat=True))


def categorias_efetivas_request(request):
    """Categorias para filtro/export; operador CRM usa sempre o escopo do perfil."""
    escopo = categorias_escopo_usuario(request.user)
    if escopo is not None:
        return list(escopo)
    from .perfil_municipe import parse_categoria_ids_from_request

    return parse_categoria_ids_from_request(request) or None


def aplicar_escopo_municipes_queryset(queryset, user, *, categoria_ids_opcional=None):
    """Restringe munícipes por conta compartilhada e, para Operador CRM, por categoria."""
    if user.is_superuser:
        if categoria_ids_opcional:
            contas_escopo = None
            queryset = filtrar_municipes_por_categoria_perfis(
                queryset, categoria_ids_opcional, contas_escopo
            )
        return queryset

    if not usuario_tem_acesso_contatos(user):
        return queryset.none()

    if not hasattr(user, "perfil"):
        return queryset.none()

    contas_escopo = set(user.perfil.contas.values_list("id", flat=True))
    if not contas_escopo:
        return queryset.none()

    queryset = queryset.filter(contas__in=contas_escopo).distinct()

    cat_escopo = categorias_escopo_usuario(user)
    if cat_escopo is not None:
        if not cat_escopo:
            return queryset.none()
        ids = list(cat_escopo)
        if categoria_ids_opcional:
            ids = [c for c in categoria_ids_opcional if c in cat_escopo]
            if not ids:
                return queryset.none()
        queryset = filtrar_municipes_por_categoria_perfis(queryset, ids, contas_escopo)
    elif categoria_ids_opcional:
        queryset = filtrar_municipes_por_categoria_perfis(
            queryset, categoria_ids_opcional, contas_escopo
        )

    return queryset


def usuario_pode_editar_municipe(user, municipe: Municipe) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    if not usuario_tem_acesso_contatos(user) or not hasattr(user, "perfil"):
        return False

    user_contas = set(user.perfil.contas.values_list("id", flat=True))
    municipe_contas = set(municipe.contas.values_list("id", flat=True))
    if user_contas.isdisjoint(municipe_contas):
        return False

    cat_escopo = categorias_escopo_usuario(user)
    if cat_escopo is None:
        return True

    if not cat_escopo:
        return False

    return municipe.perfis.filter(
        ativo=True,
        conta_id__in=user_contas,
        categoria_id__in=cat_escopo,
    ).exists()
