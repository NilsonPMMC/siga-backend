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
    
    # Lista de endpoints possíveis para tentar
    endpoints_possiveis = [
        '/organograma/',
        '/secretarias/',
        '/estrutura-organizacional/',
        '/orgaos/',
        '/departamentos/',
    ]
    
    headers = {
        'Authorization': f'Bearer {SINAPSE_API_TOKEN}',
        'Content-Type': 'application/json',
    }
    
    # Tenta cada endpoint possível
    for endpoint_path in endpoints_possiveis:
        try:
            endpoint = f"{SINAPSE_API_BASE_URL}{endpoint_path}"
            logger.info(f"Tentando buscar da API Sinapse: {endpoint}")
            
            response = requests.get(
                endpoint,
                headers=headers,
                timeout=SINAPSE_API_TIMEOUT
            )
            
            logger.info(f"Resposta da API Sinapse - Status: {response.status_code}, URL: {endpoint}")
            
            # Se retornou 404, tenta próximo endpoint
            if response.status_code == 404:
                logger.debug(f"Endpoint {endpoint} retornou 404, tentando próximo...")
                continue
            
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"Resposta da API Sinapse recebida - Tipo: {type(data)}, Tamanho: {len(data) if isinstance(data, (list, dict)) else 'N/A'}")
            
            # Normalizar resposta conforme estrutura real da API
            # Ajustar conforme documentação do Swagger
            if isinstance(data, list):
                if len(data) > 0:
                    logger.info(f"Retornando {len(data)} itens da API Sinapse")
                    return data
                else:
                    logger.warning(f"API retornou lista vazia do endpoint {endpoint}")
                    continue
            elif isinstance(data, dict):
                if 'results' in data:
                    results = data['results']
                    if len(results) > 0:
                        logger.info(f"Retornando {len(results)} itens do campo 'results'")
                        return results
                if 'data' in data:
                    results = data['data']
                    if len(results) > 0:
                        logger.info(f"Retornando {len(results)} itens do campo 'data'")
                        return results
                logger.warning(f"Resposta dict sem 'results' ou 'data' do endpoint {endpoint}: {list(data.keys())}")
            else:
                logger.warning(f"Formato de resposta inesperado da API Sinapse: {type(data)}")
                
        except requests.exceptions.Timeout:
            logger.error(f"Timeout ao buscar estrutura organizacional da API Sinapse no endpoint {endpoint}")
            continue
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug(f"Endpoint {endpoint} não encontrado (404), tentando próximo...")
                continue
            logger.error(f"Erro HTTP {e.response.status_code} ao buscar estrutura organizacional: {str(e)}")
            # Se não for 404, pode ser erro de autenticação ou outro problema
            if e.response.status_code == 401:
                raise SinapseAPIError(f"Erro de autenticação (401) - Token inválido ou não autorizado")
            elif e.response.status_code == 403:
                raise SinapseAPIError(f"Erro de permissão (403) - Token sem permissão para acessar este recurso")
            continue
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro ao buscar estrutura organizacional do endpoint {endpoint}: {str(e)}")
            continue
        except Exception as e:
            logger.error(f"Erro inesperado ao buscar estrutura organizacional: {str(e)}", exc_info=True)
            continue
    
    # Se chegou aqui, nenhum endpoint funcionou
    logger.error("Nenhum endpoint da API Sinapse retornou dados válidos")
    raise SinapseAPIError("Nenhum endpoint da API Sinapse retornou dados válidos. Verifique a documentação do Swagger.")


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
