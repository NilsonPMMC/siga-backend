"""
Tasks do Celery para processamento assíncrono de atendimentos.
"""
import logging
from celery import shared_task
from django.db import transaction

from .models import Atendimento
from .services.ai_service import AIService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def task_gerar_resumo_atendimento(self, atendimento_id):
    """
    Task assíncrona para gerar resumo de atendimento usando Gemini AI.
    
    Args:
        atendimento_id (int): ID do atendimento a processar
    
    Returns:
        bool: True se o resumo foi gerado com sucesso, False caso contrário
    """
    try:
        # Recuperar o atendimento e suas tramitações
        atendimento = Atendimento.objects.select_related(
            'municipe', 'conta', 'responsavel'
        ).prefetch_related(
            'tramitacoes'
        ).get(id=atendimento_id)
        
        logger.info(f"Iniciando geração de resumo para atendimento {atendimento.protocolo} (ID: {atendimento_id})")
        
        # Instanciar o serviço de IA
        ai_service = AIService()
        
        # Buscar tramitações ordenadas por data (mais recentes primeiro)
        tramitacoes = atendimento.tramitacoes.all().order_by('-data_tramitacao')
        
        # Gerar o resumo
        resumo = ai_service.gerar_resumo_atendimento(
            titulo=atendimento.titulo,
            descricao=atendimento.descricao,
            tramitacoes=tramitacoes
        )
        
        if resumo:
            # Salvar o resumo no campo resumo_ia
            with transaction.atomic():
                atendimento.resumo_ia = resumo
                atendimento.save(update_fields=['resumo_ia'])
            
            logger.info(f"Resumo gerado e salvo com sucesso para atendimento {atendimento.protocolo}")
            return True
        else:
            logger.warning(f"Não foi possível gerar resumo para atendimento {atendimento.protocolo}. API pode estar indisponível.")
            return False
            
    except Atendimento.DoesNotExist:
        logger.error(f"Atendimento com ID {atendimento_id} não encontrado.")
        return False
        
    except Exception as e:
        logger.error(f"Erro ao gerar resumo para atendimento ID {atendimento_id}: {e}", exc_info=True)
        # Retry com backoff exponencial em caso de erro temporário
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
