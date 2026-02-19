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
        else:
            try:
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel('gemini-pro')
                logger.info("AIService inicializado com sucesso.")
            except Exception as e:
                logger.error(f"Erro ao inicializar AIService: {e}")
                self.model = None
    
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
            logger.error(f"Erro ao gerar resumo com Gemini AI: {e}", exc_info=True)
            return None
