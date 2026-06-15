"""
Validação e detecção de payloads maliciosos em campos de checklist (observações, nomes).
Usado na API pública, nos serializers e nos comandos de saneamento.
"""
import re

# Texto livre de evento: limite razoável (evita armazenamento de megabytes)
MAX_OBSERVACOES_LEN = 4000
MAX_NOME_RESPONSAVEL_LEN = 200
MAX_NOME_ITEM_MESTRE_LEN = 255

# Padrões típicos de scanners (SQLi, XSS, OOB, command injection)
_ATTACK_REGEXES = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"bxss\.me",
        r"(\b|\|)nslookup\b",
        r"\bcurl\b\s+",
        r"\bwget\b\s+",
        r";\s*--\s*$",
        r"\bunion\s+select\b",
        r"\bsleep\s*\(",
        r"\bbenchmark\s*\(",
        r"\bwaitfor\s+delay\b",
        r"\bxor\s*\(",
        r"information_schema",
        r"@@version\b",
        r"<script[\s/>]",
        r"javascript\s*:",
        r"on\w+\s*=",
        r"eval\s*\(",
        r"!\s*\(\s*\)\s*&&\s*!",
        r"!\(\(\)\s*&&\s*!",  # variação !(()&&! comum em fuzzers
        r"\|\s*\*\s*\|\s*\*\s*\|",  # ruído tipo |*|*|
        r"\$\(\s*nslookup",
        r"\|\|\s*curl\b",
        r"sys\.databases",
        r"pg_sleep\s*\(",
        r"\$\{\@",  # probes estilo template (${@PRINT(...)} )
        r"\bprint\s*\(\s*md5\s*\(",
    )
)


def observacoes_parece_ataque(valor) -> bool:
    """True se o texto contiver assinatura conhecida de ataque/scanner."""
    if not valor or not isinstance(valor, str):
        return False
    texto = valor.strip()
    if not texto:
        return False
    for rx in _ATTACK_REGEXES:
        if rx.search(texto):
            return True
    return False


def validar_observacoes_checklist(valor) -> str:
    """
    Normaliza e valida observações para persistência.
    Levanta django.core.exceptions.ValidationError se inválido ou suspeito.
    """
    from django.core.exceptions import ValidationError

    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise ValidationError("Observações devem ser texto.")
    texto = valor.replace("\x00", "").strip()
    if len(texto) > MAX_OBSERVACOES_LEN:
        raise ValidationError(f"Observações excedem {MAX_OBSERVACOES_LEN} caracteres.")
    if observacoes_parece_ataque(texto):
        raise ValidationError("O texto das observações contém padrões não permitidos.")
    return texto


def validar_nome_item_mestre_checklist(valor) -> str:
    """Nome do item mestre (ChecklistItem): curto, sem padrões de scan/exploit."""
    from django.core.exceptions import ValidationError

    if not valor or not isinstance(valor, str):
        raise ValidationError("O nome do item é obrigatório.")
    texto = valor.replace("\x00", "").strip()
    if len(texto) < 2:
        raise ValidationError("O nome do item é muito curto.")
    if len(texto) > MAX_NOME_ITEM_MESTRE_LEN:
        raise ValidationError(f"O nome do item excede {MAX_NOME_ITEM_MESTRE_LEN} caracteres.")
    if observacoes_parece_ataque(texto):
        raise ValidationError("O nome contém padrões não permitidos.")
    return texto


def validar_nome_responsavel_checklist(valor) -> str:
    from django.core.exceptions import ValidationError

    if not valor or not isinstance(valor, str):
        raise ValidationError("Nome do responsável é obrigatório.")
    texto = valor.replace("\x00", "").strip()
    if len(texto) < 2:
        raise ValidationError("Nome do responsável muito curto.")
    if len(texto) > MAX_NOME_RESPONSAVEL_LEN:
        raise ValidationError(f"Nome do responsável excede {MAX_NOME_RESPONSAVEL_LEN} caracteres.")
    if observacoes_parece_ataque(texto):
        raise ValidationError("O nome do responsável contém padrões não permitidos.")
    return texto
