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
        logger.warning("SINAPSE_API_TOKEN não configurada. Verifique o arquivo .env")
        raise SinapseAPIError("SINAPSE_API_TOKEN não configurada no .env")
    
    logger.info(f"Iniciando busca na API Sinapse - Base URL: {SINAPSE_API_BASE_URL}")
    
    # Endpoint correto conforme Swagger: /api/v1/unidades/
    endpoint = f"{SINAPSE_API_BASE_URL}/v1/unidades/"
    
    headers = {
        'Authorization': f'Bearer {SINAPSE_API_TOKEN}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    
    try:
        logger.info(f"Buscando unidades da API Sinapse: {endpoint}")
        
        response = requests.get(
            endpoint,
            headers=headers,
            timeout=SINAPSE_API_TIMEOUT
        )
        
        logger.info(f"Resposta da API Sinapse - Status: {response.status_code}")
        
        response.raise_for_status()
        
        data = response.json()
        logger.info(f"Resposta da API Sinapse recebida - Tipo: {type(data)}, Tamanho: {len(data) if isinstance(data, list) else 'N/A'}")
        
        # A API retorna uma lista direta de unidades
        if isinstance(data, list):
            # Filtra apenas unidades ativas
            unidades_ativas = [u for u in data if u.get('ativo', True)]
            logger.info(f"Retornando {len(unidades_ativas)} unidades ativas da API Sinapse")
            return unidades_ativas
        else:
            logger.warning(f"Formato de resposta inesperado da API Sinapse: {type(data)}")
            return []
            
    except requests.exceptions.Timeout:
        logger.error("Timeout ao buscar unidades da API Sinapse")
        raise SinapseAPIError("Timeout ao conectar com a API Sinapse")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            logger.error("Erro de autenticação (401) - Token inválido ou não autorizado")
            raise SinapseAPIError("Erro de autenticação (401) - Token inválido ou não autorizado")
        elif e.response.status_code == 403:
            logger.error("Erro de permissão (403) - Token sem permissão para acessar este recurso")
            raise SinapseAPIError("Erro de permissão (403) - Token sem permissão para acessar este recurso")
        else:
            logger.error(f"Erro HTTP {e.response.status_code} ao buscar unidades: {str(e)}")
            raise SinapseAPIError(f"Erro HTTP {e.response.status_code} ao buscar unidades da API Sinapse")
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao buscar unidades da API Sinapse: {str(e)}")
        raise SinapseAPIError(f"Erro ao conectar com a API Sinapse: {str(e)}")
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar unidades: {str(e)}", exc_info=True)
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
        
        # Endpoint correto conforme Swagger: /api/v1/unidades/{id}/
        endpoint = f"{SINAPSE_API_BASE_URL}/v1/unidades/{sinapse_id}/"
        
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
