"""
Validações para transições de status de atendimentos.
"""
from rest_framework.exceptions import ValidationError


# Regras de transição de status
TRANSICOES_PERMITIDAS = {
    'ABERTO': ['EM_ANALISE', 'ENCAMINHADO', 'ARQUIVADO'],
    'EM_ANALISE': ['ABERTO', 'ENCAMINHADO', 'CONCLUIDO', 'ARQUIVADO'],
    'ENCAMINHADO': ['EM_ANALISE', 'CONCLUIDO', 'ARQUIVADO'],
    'CONCLUIDO': ['ARQUIVADO'],  # Concluído só pode ir para Arquivado
    'ARQUIVADO': [],  # Arquivado é estado final
}


def validar_transicao_status(status_atual: str, status_novo: str) -> bool:
    """
    Valida se a transição de status é permitida.
    
    Args:
        status_atual: Status atual do atendimento
        status_novo: Novo status desejado
        
    Returns:
        True se a transição é permitida
        
    Raises:
        ValidationError: Se a transição não é permitida
    """
    if status_atual == status_novo:
        raise ValidationError("O novo status deve ser diferente do status atual.")
    
    statuses_permitidos = TRANSICOES_PERMITIDAS.get(status_atual, [])
    
    if status_novo not in statuses_permitidos:
        status_display = dict([
            ('ABERTO', 'Aberto'),
            ('EM_ANALISE', 'Em Análise'),
            ('ENCAMINHADO', 'Encaminhado'),
            ('CONCLUIDO', 'Concluído'),
            ('ARQUIVADO', 'Arquivado'),
        ])
        
        raise ValidationError(
            f"Não é possível alterar o status de '{status_display.get(status_atual, status_atual)}' "
            f"para '{status_display.get(status_novo, status_novo)}'. "
            f"Transições permitidas: {', '.join([status_display.get(s, s) for s in statuses_permitidos])}"
        )
    
    return True


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
