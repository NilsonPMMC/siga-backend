"""
Serviço de integração com a API Sinapse (Mogi das Cruzes).
Fornece métodos para buscar estrutura organizacional (secretarias, órgãos, etc).
"""
import requests
from django.conf import settings
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

# Configurações da API Sinapse
SINAPSE_API_BASE_URL = getattr(settings, 'SINAPSE_API_BASE_URL', 'https://api.mogidascruzes.sp.gov.br/api')
SINAPSE_API_TOKEN = getattr(settings, 'SINAPSE_API_TOKEN', None)
SINAPSE_API_TIMEOUT = getattr(settings, 'SINAPSE_API_TIMEOUT', 10)


class SinapseAPIError(Exception):
    """Exceção customizada para erros da API Sinapse"""
    pass


def buscar_estrutura_organizacional() -> List[Dict]:
    """
    Busca estrutura organizacional completa da API Sinapse.
    
    Retorna lista de secretarias/órgãos com hierarquia.
    Formato esperado:
    [
        {
            'id': 123,
            'nome': 'Secretaria de Educação',
            'sigla': 'SEDUC',
            'tipo': 'Secretaria',
            'hierarquia': {...}
        },
        ...
    ]
    
    Raises:
        SinapseAPIError: Se houver erro na comunicação com a API
    """
    if not SINAPSE_API_TOKEN:
        logger.warning("SINAPSE_API_TOKEN não configurada. Retornando lista vazia.")
        return []
    
    try:
        headers = {
            'Authorization': f'Bearer {SINAPSE_API_TOKEN}',
            'Content-Type': 'application/json',
        }
        
        # Endpoint provável - ajustar conforme documentação real da API
        # Possíveis endpoints:
        # - /api/organograma/
        # - /api/secretarias/
        # - /api/estrutura-organizacional/
        endpoint = f"{SINAPSE_API_BASE_URL}/organograma/"
        
        response = requests.get(
            endpoint,
            headers=headers,
            timeout=SINAPSE_API_TIMEOUT
        )
        
        response.raise_for_status()
        
        data = response.json()
        
        # Normalizar resposta conforme estrutura real da API
        # Ajustar conforme documentação do Swagger
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and 'results' in data:
            return data['results']
        elif isinstance(data, dict) and 'data' in data:
            return data['data']
        else:
            logger.warning(f"Formato de resposta inesperado da API Sinapse: {type(data)}")
            return []
            
    except requests.exceptions.Timeout:
        logger.error("Timeout ao buscar estrutura organizacional da API Sinapse")
        raise SinapseAPIError("Timeout ao conectar com a API Sinapse")
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao buscar estrutura organizacional: {str(e)}")
        raise SinapseAPIError(f"Erro ao conectar com a API Sinapse: {str(e)}")
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar estrutura organizacional: {str(e)}")
        raise SinapseAPIError(f"Erro inesperado: {str(e)}")


def buscar_secretaria_por_id(sinapse_id: int) -> Optional[Dict]:
    """
    Busca uma secretaria específica por ID na API Sinapse.
    
    Args:
        sinapse_id: ID da secretaria na API Sinapse
        
    Returns:
        Dict com dados da secretaria ou None se não encontrada
        
    Raises:
        SinapseAPIError: Se houver erro na comunicação com a API
    """
    if not SINAPSE_API_TOKEN:
        logger.warning("SINAPSE_API_TOKEN não configurada.")
        return None
    
    try:
        headers = {
            'Authorization': f'Bearer {SINAPSE_API_TOKEN}',
            'Content-Type': 'application/json',
        }
        
        # Endpoint provável - ajustar conforme documentação real
        endpoint = f"{SINAPSE_API_BASE_URL}/organograma/{sinapse_id}/"
        
        response = requests.get(
            endpoint,
            headers=headers,
            timeout=SINAPSE_API_TIMEOUT
        )
        
        response.raise_for_status()
        
        return response.json()
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao buscar secretaria {sinapse_id} da API Sinapse")
        raise SinapseAPIError("Timeout ao conectar com a API Sinapse")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise SinapseAPIError(f"Erro HTTP ao buscar secretaria: {str(e)}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao buscar secretaria {sinapse_id}: {str(e)}")
        raise SinapseAPIError(f"Erro ao conectar com a API Sinapse: {str(e)}")
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar secretaria: {str(e)}")
        raise SinapseAPIError(f"Erro inesperado: {str(e)}")


def validar_secretaria_sinapse(sinapse_id: int) -> bool:
    """
    Valida se uma secretaria existe na API Sinapse.
    
    Args:
        sinapse_id: ID da secretaria na API Sinapse
        
    Returns:
        True se existe, False caso contrário
    """
    try:
        secretaria = buscar_secretaria_por_id(sinapse_id)
        return secretaria is not None
    except SinapseAPIError:
        return False
