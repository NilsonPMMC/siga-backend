"""
Tasks do Celery para processamento assíncrono de atendimentos e municipes.
"""
import logging
from celery import shared_task
from django.db import transaction

from .models import Atendimento, Municipe
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


@shared_task(bind=True, max_retries=2)
def task_auditar_qualidade_municipe(self, municipe_id):
    """
    Task assíncrona para auditar qualidade de dados de um munícipe usando Gemini AI.
    
    Args:
        municipe_id (int): ID do munícipe a auditar
    
    Returns:
        bool: True se a auditoria foi concluída com sucesso, False caso contrário
    """
    try:
        municipe = Municipe.objects.select_related('categoria').get(id=municipe_id)
        
        logger.info(f"Iniciando auditoria de qualidade para munícipe {municipe.nome_completo} (ID: {municipe_id})")
        
        # Extrair dados essenciais
        telefone_principal = ''
        if municipe.telefones and isinstance(municipe.telefones, list) and len(municipe.telefones) > 0:
            telefone_principal = municipe.telefones[0].get('numero', '')
        
        bairro = ''
        if municipe.endereco and isinstance(municipe.endereco, dict):
            bairro = municipe.endereco.get('bairro', '')
        
        dados = {
            'nome_completo': municipe.nome_completo or '',
            'telefone': telefone_principal,
            'cpf': municipe.cpf or '',
            'categoria': municipe.categoria.nome if municipe.categoria else '',
            'orgao': municipe.orgao or '',
            'bairro': bairro,
        }
        
        # Instanciar serviço de IA
        ai_service = AIService()
        
        # Gerar análise de qualidade
        resultado = ai_service.analisar_qualidade_registro(dados)
        
        if resultado:
            # Salvar resultado no campo auditoria_ia
            with transaction.atomic():
                municipe.auditoria_ia = resultado
                municipe.save(update_fields=['auditoria_ia'])
            
            logger.info(f"Auditoria concluída para munícipe {municipe.nome_completo}. Classificação: {resultado.get('classificacao')}, Nota: {resultado.get('nota_qualidade')}")
            return True
        else:
            logger.warning(f"Não foi possível gerar auditoria para munícipe {municipe.nome_completo}. API pode estar indisponível.")
            return False
            
    except Municipe.DoesNotExist:
        logger.error(f"Munícipe com ID {municipe_id} não encontrado.")
        return False
        
    except Exception as e:
        logger.error(f"Erro ao auditar qualidade do munícipe ID {municipe_id}: {e}", exc_info=True)
        # Retry apenas uma vez para auditoria (não é crítico)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30)
        return False
