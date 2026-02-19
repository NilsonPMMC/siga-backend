"""
Serviço de integração com Google Gemini AI para geração de resumos automáticos.
"""
import logging
from django.conf import settings
import google.generativeai as genai

logger = logging.getLogger(__name__)


class AIService:
    """Serviço para interação com Google Gemini AI."""
    
    def __init__(self):
        """Inicializa o serviço com a API key do Gemini."""
        api_key = getattr(settings, 'GEMINI_API_KEY', None)
        if not api_key:
            logger.warning("GEMINI_API_KEY não configurada. Funcionalidades de IA não estarão disponíveis.")
            self.model = None
            self.model_name = None
        else:
            try:
                genai.configure(api_key=api_key)
                # Usar gemini-1.5-flash (mais rápido e econômico) ou gemini-1.5-pro (mais preciso)
                self.model_name = 'gemini-1.5-flash'
                self.model = genai.GenerativeModel(self.model_name)
                logger.info(f"AIService inicializado com sucesso usando modelo {self.model_name}.")
            except Exception as e:
                logger.error(f"Erro ao inicializar AIService: {e}", exc_info=True)
                self.model = None
                self.model_name = None
    
    def gerar_resumo_atendimento(self, titulo, descricao, tramitacoes):
        """
        Gera um resumo automático do atendimento usando Gemini AI.
        
        Args:
            titulo (str): Título do atendimento
            descricao (str): Descrição detalhada do atendimento
            tramitacoes (QuerySet ou list): Lista de tramitações relacionadas
        
        Returns:
            str: Resumo gerado pela IA ou None em caso de erro
        """
        if not self.model:
            logger.warning("Modelo Gemini não disponível. Não é possível gerar resumo.")
            return None
        
        try:
            # Preparar texto das tramitações
            tramitacoes_texto = ""
            if tramitacoes:
                tramitacoes_lista = list(tramitacoes[:10])  # Limitar a 10 tramitações mais recentes
                for tram in tramitacoes_lista:
                    data_str = tram.data_tramitacao.strftime('%d/%m/%Y %H:%M')
                    status_info = ""
                    if tram.alterou_status:
                        status_info = f" [Status alterado: {tram.get_status_anterior_display() if tram.status_anterior else 'N/A'} → {tram.get_status_novo_display() if tram.status_novo else 'N/A'}]"
                    tramitacoes_texto += f"\n- {data_str}: {tram.despacho}{status_info}"
            
            # Construir prompt estruturado
            prompt = f"""Você é um consultor especializado em gestão pública e análise de processos administrativos.

Analise o seguinte atendimento público e gere um resumo executivo conciso (máximo 3 parágrafos) que destaque:

1. A natureza da solicitação/demanda
2. O histórico de tramitação e evolução do processo
3. O status atual e próximos passos (se aplicável)

**Título do Atendimento:**
{titulo}

**Descrição:**
{descricao}

**Histórico de Tramitações:**
{tramitacoes_texto if tramitacoes_texto else "Nenhuma tramitação registrada ainda."}

**Instruções:**
- Seja objetivo e profissional
- Use linguagem clara e acessível
- Destaque informações relevantes para gestão pública
- Máximo de 3 parágrafos
- Não invente informações que não estejam nos dados fornecidos

Gere o resumo agora:"""

            # Chamar a API do Gemini
            response = self.model.generate_content(prompt)
            
            if response and response.text:
                resumo = response.text.strip()
                logger.info(f"Resumo gerado com sucesso. Tamanho: {len(resumo)} caracteres.")
                return resumo
            else:
                logger.warning("Resposta do Gemini vazia ou inválida.")
                return None
                
        except Exception as e:
            error_msg = str(e)
            # Tratamento específico para erros comuns da API
            if '404' in error_msg or 'NotFound' in error_msg:
                logger.error(f"Modelo Gemini não encontrado (404). Verifique se o modelo {self.model_name} está disponível.")
            elif '500' in error_msg or 'InternalServerError' in error_msg:
                logger.error("Erro interno do servidor Gemini (500). Tente novamente mais tarde.")
            else:
                logger.error(f"Erro ao gerar resumo com Gemini AI: {error_msg}")
            return None
    
    def analisar_qualidade_registro(self, dados_json):
        """
        Analisa a qualidade de um registro de Munícipe usando Gemini AI.
        
        Args:
            dados_json (dict): Dicionário com campos do Munícipe:
                - nome_completo
                - telefone (primeiro da lista)
                - cpf
                - categoria
                - orgao
                - bairro (do endereco)
        
        Returns:
            dict: {
                'classificacao': 'SAD' | 'OK' | 'FALSO_POSITIVO',
                'nota_qualidade': int (0-10),
                'sugestao_correcao': str,
                'problemas_detectados': list[str]
            } ou None em caso de erro
        """
        if not self.model:
            logger.warning("Modelo Gemini não disponível. Não é possível analisar qualidade.")
            return None
        
        try:
            nome = dados_json.get('nome_completo', '')
            telefone = dados_json.get('telefone', '')
            cpf = dados_json.get('cpf', '')
            categoria = dados_json.get('categoria', '')
            orgao = dados_json.get('orgao', '')
            bairro = dados_json.get('bairro', '')
            
            prompt = f"""Você é um especialista em qualidade de dados de CRM e gestão pública.

Analise o seguinte registro de contato e classifique sua qualidade:

**Dados do Registro:**
- Nome: {nome}
- Telefone: {telefone if telefone else 'Não informado'}
- CPF: {cpf if cpf else 'Não informado'}
- Categoria: {categoria if categoria else 'Não informado'}
- Órgão/Empresa: {orgao if orgao else 'Não informado'}
- Bairro: {bairro if bairro else 'Não informado'}

**Instruções:**
Classifique este registro em UMA das seguintes categorias:
- "SAD": Dados pobres/incompletos (sem CPF, telefone genérico/inválido, nome fictício ou incompleto)
- "OK": Dados aceitáveis (informações válidas e completas)
- "FALSO_POSITIVO": Telefones genéricos como (99) 99999-9999, sequências repetitivas (00000000, 99999999), sequências óbvias (12345678), ou nomes claramente fictícios

**Critérios de Baixa Qualidade:**
- Telefones genéricos ou inválidos (padrões como 99999-9999, 00000-0000, 12345-6789)
- CPF ausente ou inválido
- Nomes fictícios, incompletos ou genéricos (ex: "TESTE", "FULANO", "NOME COMPLETO")
- Ausência de informações essenciais (telefone E CPF ausentes)

Responda APENAS em formato JSON válido, sem markdown, com a seguinte estrutura:
{{
    "classificacao": "SAD" | "OK" | "FALSO_POSITIVO",
    "nota_qualidade": <número inteiro de 0 a 10>,
    "sugestao_correcao": "<texto breve com sugestão de correção ou 'Nenhuma correção necessária'>",
    "problemas_detectados": ["<problema 1>", "<problema 2>", ...]
}}"""

            response = self.model.generate_content(prompt)
            
            if response and response.text:
                import json
                import re
                # Extrair JSON da resposta (pode vir com markdown ou texto extra)
                texto = response.text.strip()
                # Tentar encontrar JSON no texto (suporta JSON aninhado)
                json_match = re.search(r'\{.*\}', texto, re.DOTALL)
                if json_match:
                    try:
                        resultado = json.loads(json_match.group())
                        logger.info(f"Análise de qualidade concluída. Classificação: {resultado.get('classificacao')}, Nota: {resultado.get('nota_qualidade')}")
                        return resultado
                    except json.JSONDecodeError:
                        logger.warning("Resposta do Gemini contém JSON inválido.")
                        return None
                else:
                    logger.warning("Resposta do Gemini não contém JSON válido.")
                    return None
            else:
                logger.warning("Resposta do Gemini vazia ou inválida.")
                return None
                
        except Exception as e:
            error_msg = str(e)
            # Tratamento específico para erros da API - não expor traceback completo
            if '404' in error_msg or 'NotFound' in error_msg:
                logger.error(f"Modelo Gemini não encontrado (404). Verifique se o modelo {self.model_name} está disponível.")
            elif '500' in error_msg or 'InternalServerError' in error_msg:
                logger.error("Erro interno do servidor Gemini (500). API temporariamente indisponível.")
            elif '429' in error_msg or 'QuotaExceeded' in error_msg:
                logger.error("Quota da API Gemini excedida. Aguarde antes de tentar novamente.")
            else:
                logger.error(f"Erro ao analisar qualidade do registro com Gemini AI: {error_msg}")
            # Retornar None para permitir fallback para detecção regex
            return None
    
    def sugerir_fusao(self, registro_a, lista_possiveis):
        """
        Compara um registro suspeito com outros possíveis duplicados e sugere se devem ser fundidos.
        
        Args:
            registro_a (dict): Dados do registro principal:
                - nome_completo
                - telefone
                - categoria
                - bairro
                - cpf
            lista_possiveis (list[dict]): Lista de até 5 registros possíveis para comparação
                Cada dict deve ter: nome_completo, telefone, categoria, bairro, cpf
        
        Returns:
            dict: {
                'deve_fundir': bool,
                'confianca': float (0.0 a 1.0),
                'justificativa': str,
                'registros_similares': list[int] (índices dos registros que devem ser fundidos)
            } ou None em caso de erro
        """
        if not self.model:
            logger.warning("Modelo Gemini não disponível. Não é possível sugerir fusão.")
            return None
        
        if not lista_possiveis or len(lista_possiveis) == 0:
            return {'deve_fundir': False, 'confianca': 0.0, 'justificativa': 'Nenhum registro para comparar', 'registros_similares': []}
        
        try:
            # Preparar texto dos registros possíveis
            registros_texto = ""
            for idx, reg in enumerate(lista_possiveis[:5], 1):  # Limitar a 5 registros
                registros_texto += f"""
Registro {idx}:
- Nome: {reg.get('nome_completo', 'N/A')}
- Telefone: {reg.get('telefone', 'N/A')}
- CPF: {reg.get('cpf', 'N/A')}
- Categoria: {reg.get('categoria', 'N/A')}
- Bairro: {reg.get('bairro', 'N/A')}
"""
            
            prompt = f"""Você é um especialista em deduplicação de dados de CRM.

Compare o registro principal com os registros possíveis abaixo e determine se são a mesma pessoa física.

**Registro Principal:**
- Nome: {registro_a.get('nome_completo', 'N/A')}
- Telefone: {registro_a.get('telefone', 'N/A')}
- CPF: {registro_a.get('cpf', 'N/A')}
- Categoria: {registro_a.get('categoria', 'N/A')}
- Bairro: {registro_a.get('bairro', 'N/A')}

**Registros Possíveis para Comparação:**
{registros_texto}

**Instruções:**
Analise se algum dos registros possíveis representa a MESMA PESSOA FÍSICA que o registro principal.
Considere:
- Nomes similares (variações, abreviações, erros de digitação)
- Mesmo telefone ou telefones relacionados
- Mesmo CPF (se disponível)
- Mesma categoria e localização (bairro)
- Contexto de gestão pública (mesma pessoa pode ter múltiplos registros por erro)

Responda APENAS em formato JSON válido, sem markdown:
{{
    "deve_fundir": <true ou false>,
    "confianca": <número decimal de 0.0 a 1.0 indicando confiança na sugestão>,
    "justificativa": "<explicação breve do motivo>",
    "registros_similares": [<lista de índices (1, 2, 3...) dos registros que devem ser fundidos>]
}}"""

            response = self.model.generate_content(prompt)
            
            if response and response.text:
                import json
                import re
                texto = response.text.strip()
                json_match = re.search(r'\{.*\}', texto, re.DOTALL)
                if json_match:
                    try:
                        resultado = json.loads(json_match.group())
                        logger.info(f"Sugestão de fusão gerada. Deve fundir: {resultado.get('deve_fundir')}, Confiança: {resultado.get('confianca')}")
                        return resultado
                    except json.JSONDecodeError:
                        logger.warning("Resposta do Gemini contém JSON inválido para sugestão de fusão.")
                        return None
                else:
                    logger.warning("Resposta do Gemini não contém JSON válido para sugestão de fusão.")
                    return None
            else:
                logger.warning("Resposta do Gemini vazia ou inválida para sugestão de fusão.")
                return None
                
        except Exception as e:
            error_msg = str(e)
            # Tratamento específico para erros da API
            if '404' in error_msg or 'NotFound' in error_msg:
                logger.error(f"Modelo Gemini não encontrado (404). Verifique se o modelo {self.model_name} está disponível.")
            elif '500' in error_msg or 'InternalServerError' in error_msg:
                logger.error("Erro interno do servidor Gemini (500). API temporariamente indisponível.")
            elif '429' in error_msg or 'QuotaExceeded' in error_msg:
                logger.error("Quota da API Gemini excedida. Aguarde antes de tentar novamente.")
            else:
                logger.error(f"Erro ao sugerir fusão com Gemini AI: {error_msg}")
            return None
