"""
Pipeline unificado de análise IA para um atendimento: resumo, embedding e classificação de assunto.
"""
from typing import Any, Dict, Optional

from django.db import transaction

from ..models import Atendimento
from .assunto_ia import sugerir_assunto_atendimento
from .ia_intelligence import atualizar_vetor_atendimento, gerar_resumo_atendimento


def _precisa_resumo(atendimento: Atendimento, forcar: bool) -> bool:
    if forcar:
        return True
    if atendimento.auditoria_ia_status in ('PENDENTE', 'ERRO'):
        return True
    return not (atendimento.resumo_ia_local or '').strip()


def _precisa_assunto(atendimento: Atendimento, forcar: bool) -> bool:
    if forcar:
        return True
    if atendimento.assunto_ia_status in (None, 'PENDENTE', 'ERRO'):
        return True
    return atendimento.assunto_id is None


def processar_analise_ia_atendimento(
    atendimento: Atendimento,
    *,
    aplicar_assunto: bool = False,
    processar_resumo: bool = True,
    processar_vetor: bool = True,
    processar_assunto: bool = True,
    forcar: bool = False,
) -> Dict[str, Any]:
    """
    Executa resumo + vetor + assunto IA para um atendimento.
    Retorna dict com status por etapa; não propaga exceções.
    """
    resultado: Dict[str, Any] = {
        'protocolo': atendimento.protocolo,
        'id': atendimento.id,
        'resumo': 'ignorado',
        'vetor': 'ignorado',
        'assunto': 'ignorado',
        'ok': True,
        'detalhes': [],
    }

    try:
        if processar_resumo and _precisa_resumo(atendimento, forcar):
            resumo = gerar_resumo_atendimento(atendimento)
            if resumo is None:
                atendimento.auditoria_ia_status = 'ERRO'
                atendimento.save(update_fields=['auditoria_ia_status'])
                resultado['resumo'] = 'erro'
                resultado['ok'] = False
                resultado['detalhes'].append('resumo não gerado')
            else:
                atendimento.resumo_ia_local = resumo
                atendimento.auditoria_ia_status = 'PROCESSADO'
                atendimento.save(update_fields=['resumo_ia_local', 'auditoria_ia_status'])
                resultado['resumo'] = 'ok'
                atendimento.refresh_from_db()
        elif processar_resumo:
            resultado['resumo'] = 'ja_processado'

        if processar_vetor and resultado['resumo'] in ('ok', 'ja_processado'):
            if atualizar_vetor_atendimento(atendimento):
                resultado['vetor'] = 'ok'
            else:
                resultado['vetor'] = 'erro'
                resultado['detalhes'].append('embedding não gerado')
                if resultado['resumo'] == 'ok':
                    atendimento.auditoria_ia_status = 'ERRO'
                    atendimento.save(update_fields=['auditoria_ia_status'])
                    resultado['ok'] = False

        if processar_assunto and _precisa_assunto(atendimento, forcar):
            res_assunto = sugerir_assunto_atendimento(atendimento, aplicar=aplicar_assunto)
            if res_assunto.get('ok'):
                resultado['assunto'] = 'aplicado' if res_assunto.get('aplicado') else 'sugerido'
            else:
                resultado['assunto'] = 'erro'
                resultado['detalhes'].append(res_assunto.get('erro', 'assunto IA falhou'))
        elif processar_assunto:
            resultado['assunto'] = 'ja_processado'

    except Exception as exc:
        resultado['ok'] = False
        resultado['detalhes'].append(str(exc))
        try:
            atendimento.auditoria_ia_status = 'ERRO'
            atendimento.save(update_fields=['auditoria_ia_status'])
        except Exception:
            pass

    return resultado
