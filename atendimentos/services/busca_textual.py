"""
Motor unificado de busca textual tolerante para munícipes.

Regras:
- ignora acentuação e caixa;
- ignora preposições em nomes (da, de, do, dos, das, e);
- tokens de nome em qualquer ordem;
- CPF (com ou sem máscara) e matrícula RH;
- perfil: categoria, cargo, instituição, departamento.
"""
from __future__ import annotations

import re
import unicodedata

PREPOSICOES_NOME = frozenset({"da", "de", "do", "dos", "das", "e"})
MIN_PREFIXO_PALAVRA = 3

CAMPOS_BUSCA_VALUES = (
    "id",
    "nome_completo",
    "nome_de_guerra",
    "cpf",
    "matricula_rh",
    "cargo",
    "orgao",
    "endereco",
    "emails",
    "perfis__categoria__nome",
    "perfis__cargo",
    "perfis__instituicao",
    "perfis__departamento",
)


def normalizar_texto(valor) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in texto if not unicodedata.combining(ch))


def extrair_digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def tokenizar_busca_nome(termo: str) -> list[str]:
    termo_normalizado = normalizar_texto(termo)
    return [
        token
        for token in termo_normalizado.split()
        if token and token not in PREPOSICOES_NOME
    ]


def palavras_de_texto(valor) -> list[str]:
    normalizado = normalizar_texto(valor)
    if not normalizado:
        return []
    return [
        palavra
        for palavra in normalizado.split()
        if palavra and palavra not in PREPOSICOES_NOME
    ]


def token_corresponde_palavra(token: str, palavra: str) -> bool:
    if not token or not palavra:
        return False
    if palavra == token:
        return True
    return len(token) >= MIN_PREFIXO_PALAVRA and palavra.startswith(token)


def tokens_correspondem_palavras(tokens: list[str], palavras: list[str]) -> bool:
    if not tokens:
        return False
    if not palavras:
        return False
    return all(
        any(token_corresponde_palavra(token, palavra) for palavra in palavras)
        for token in tokens
    )


def busca_parece_nome(tokens: list[str]) -> bool:
    return bool(tokens) and all(token.isalpha() for token in tokens)


def _palavras_perfil_e_cargo(item: dict) -> list[str]:
    palavras = []
    for campo in (
        item.get("cargo"),
        item.get("orgao"),
        item.get("perfis__categoria__nome"),
        item.get("perfis__cargo"),
        item.get("perfis__instituicao"),
        item.get("perfis__departamento"),
    ):
        palavras.extend(palavras_de_texto(campo))
    return palavras


def registro_corresponde_termo(item: dict, termo: str) -> bool:
    termo = (termo or "").strip()
    if not termo:
        return True

    termo_normalizado = normalizar_texto(termo)
    if not termo_normalizado:
        return False

    tokens_nome = tokenizar_busca_nome(termo)
    palavras_nome = palavras_de_texto(item.get("nome_completo")) + palavras_de_texto(
        item.get("nome_de_guerra")
    )

    if tokens_nome and tokens_correspondem_palavras(tokens_nome, palavras_nome):
        return True

    if busca_parece_nome(tokens_nome):
        palavras_perfil = _palavras_perfil_e_cargo(item)
        if tokens_correspondem_palavras(tokens_nome, palavras_perfil):
            return True
        return False

    digitos_termo = extrair_digitos(termo)
    if len(digitos_termo) >= 3:
        cpf_digitos = extrair_digitos(item.get("cpf"))
        if cpf_digitos and digitos_termo in cpf_digitos:
            return True

        matricula = normalizar_texto(item.get("matricula_rh"))
        if matricula and (
            termo_normalizado in matricula or digitos_termo in extrair_digitos(matricula)
        ):
            return True

    if len(termo_normalizado) >= 2 and not termo_normalizado.isdigit():
        matricula = normalizar_texto(item.get("matricula_rh"))
        if matricula and termo_normalizado in matricula:
            return True

    outros_campos = [
        item.get("cpf"),
        item.get("matricula_rh"),
        item.get("cargo"),
        item.get("orgao"),
        item.get("endereco"),
        item.get("emails"),
        item.get("perfis__categoria__nome"),
        item.get("perfis__cargo"),
        item.get("perfis__instituicao"),
        item.get("perfis__departamento"),
    ]
    outros_normalizados = " ".join(
        normalizar_texto(campo) for campo in outros_campos if campo is not None
    )
    if "@" in termo and termo_normalizado in outros_normalizados:
        return True
    if termo_normalizado in outros_normalizados:
        return True

    palavras_outros = _palavras_perfil_e_cargo(item)
    tokens_outros = tokenizar_busca_nome(termo)
    if tokens_outros and tokens_correspondem_palavras(tokens_outros, palavras_outros):
        return True

    return False


def filtrar_queryset_municipe(queryset, termo: str):
    termo = (termo or "").strip()
    if not termo:
        return queryset

    ids_match = set()
    for item in queryset.values(*CAMPOS_BUSCA_VALUES):
        if registro_corresponde_termo(item, termo):
            ids_match.add(item["id"])

    if not ids_match:
        return queryset.none()
    return queryset.filter(id__in=ids_match)


def _campos_atendimento_correspondem(termo: str, protocolo: str, titulo: str) -> bool:
    termo_normalizado = normalizar_texto(termo)
    if not termo_normalizado:
        return False

    campos = f"{normalizar_texto(protocolo)} {normalizar_texto(titulo)}".strip()
    if termo_normalizado in campos:
        return True

    palavras = palavras_de_texto(campos)
    tokens = tokenizar_busca_nome(termo)
    return bool(tokens) and tokens_correspondem_palavras(tokens, palavras)


def filtrar_queryset_atendimento(queryset, termo: str):
    """
    Busca tolerante em protocolo, título e munícipe vinculado
    (nome, CPF, matrícula RH, perfil).
    """
    from atendimentos.models import Municipe

    termo = (termo or "").strip()
    if not termo:
        return queryset

    municipe_ids = set()
    municipe_ids_qs = (
        queryset.exclude(municipe_id__isnull=True)
        .values_list("municipe_id", flat=True)
        .distinct()
    )
    if municipe_ids_qs:
        municipes = Municipe.objects.filter(id__in=municipe_ids_qs)
        municipe_ids = set(
            filtrar_queryset_municipe(municipes, termo).values_list("id", flat=True)
        )

    ids_match = set()
    for item in queryset.values("id", "protocolo", "titulo", "municipe_id"):
        atendimento_ok = _campos_atendimento_correspondem(
            termo,
            item.get("protocolo"),
            item.get("titulo"),
        )
        municipe_ok = item.get("municipe_id") in municipe_ids
        if atendimento_ok or municipe_ok:
            ids_match.add(item["id"])

    if not ids_match:
        return queryset.none()
    return queryset.filter(id__in=ids_match)
