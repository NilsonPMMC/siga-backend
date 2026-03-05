import re

import phonenumbers


def _count_distinct_digits(digits: str) -> int:
    return len(set(ch for ch in digits if ch.isdigit()))


def _is_sequential(digits: str) -> bool:
    seq = "0123456789"
    rev = seq[::-1]
    return digits in seq or digits in rev


def is_data_dirty(valor: str, tipo: str) -> bool:
    """
    Verifica se um dado é "sujo" ou suspeito, de acordo com o tipo.

    tipo pode ser: 'telefone' ou 'email'.
    """
    if not valor:
        return True

    val = str(valor).strip()

    if tipo == "telefone":
        # Normaliza dígitos
        digits = re.sub(r"\D", "", val)
        if not digits:
            return True

        # Telefones explicitamente banidos (temporários) considerados inválidos
        # Ex.: telefone geral do gabinete usado como placeholder em massa
        telefones_banidos = {
            "1147985000",  # (11) 4798-5000
        }
        if digits in telefones_banidos:
            return True

        # Menos de 3 dígitos distintos (ex.: 00000000, 11111111, 99999999)
        if _count_distinct_digits(digits) < 3:
            return True

        # Sequência óbvia (12345678, 0123456789, etc.)
        if _is_sequential(digits):
            return True

        # Validação usando phonenumbers (assumindo Brasil como padrão)
        try:
            parsed = phonenumbers.parse(digits, "BR")
            if not phonenumbers.is_valid_number(parsed):
                return True
        except phonenumbers.NumberParseException:
            return True

        return False

    if tipo == "email":
        email = val.lower()

        # Deve conter @ e um domínio minimamente válido
        if "@" not in email:
            return True
        local, _, domain = email.partition("@")
        if not local or not domain or "." not in domain:
            return True

        # Palavras-chave de e-mails "falsos"
        suspeitas = ("teste@", "naotem@", "sememail@", "fulano@", "teste.", ".teste")
        if any(padrao in email for padrao in suspeitas):
            return True

        return False

    # Por padrão, se não soubermos o tipo, não marcamos como sujo
    return False

