"""
Validações para atendimentos.
"""
from django.conf import settings
from rest_framework.exceptions import ValidationError


def validar_transicao_status(status_atual: str, status_novo: str) -> bool:
    """
    Valida alteração de status (sem matriz de transições restritiva).
    """
    if status_atual == status_novo:
        raise ValidationError("O novo status deve ser diferente do status atual.")
    return True


def validar_assunto_obrigatorio(assunto, instance=None):
    """
    Exige assunto quando ATENDIMENTO_ASSUNTO_OBRIGATORIO está ativo (rollout via .env).
    """
    if not getattr(settings, 'ATENDIMENTO_ASSUNTO_OBRIGATORIO', True):
        return True
    if assunto:
        return True
    raise ValidationError({'assunto_id': 'Selecione o assunto do atendimento.'})


def validar_encaminhamento(status_novo: str, dados_encaminhamento: dict) -> bool:
    """
    Valida se status ENCAMINHADO tem dados de encaminhamento obrigatórios.

    Args:
        status_novo: Novo status do atendimento
        dados_encaminhamento: Dict com dados de encaminhamento

    Returns:
        True se válido

    Raises:
        ValidationError: Se status ENCAMINHADO não tem dados obrigatórios
    """
    if status_novo == 'ENCAMINHADO':
        if not dados_encaminhamento.get('encaminhado_para_sinapse_id'):
            raise ValidationError(
                "Para status 'Encaminhado', é obrigatório informar o destino (Secretaria/Órgão) "
                "através do campo 'encaminhado_para_sinapse_id'."
            )

    return True
