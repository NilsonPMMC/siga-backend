from django.conf import settings
import requests
import json
from datetime import date
import re
import logging
from typing import List, Dict, Optional, Any, Tuple
import numpy as np
from openai import OpenAI

logger = logging.getLogger(__name__)

# --- CONFIGURAÇÕES GERAIS ---
# Geração de texto (padrão OpenAI / Groq)
OPENAI_API_KEY = getattr(settings, 'OPENAI_API_KEY', '')
OPENAI_API_BASE = getattr(settings, 'OPENAI_API_BASE', 'https://api.groq.com/openai/v1')
LLM_MODEL = getattr(settings, 'LLM_MODEL', 'llama-3.3-70b-versatile')

# Embeddings (kernel local compatível OpenAI)
AI_KERNEL_URL = getattr(settings, 'AI_KERNEL_URL', 'http://192.168.10.50:8004/v1')
AI_KERNEL_EMBEDDING_MODEL = getattr(settings, 'AI_KERNEL_EMBEDDING_MODEL', 'mxbai-embed-large')

# Cliente OpenAI apontando para a nuvem (Groq)
llm_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_API_BASE) if OPENAI_API_KEY else None

# Cliente OpenAI apontando para o Kernel Local (Embeddings)
kernel_client = OpenAI(
    api_key="sk-local-dummy",  # Maioria dos kernels locais ignora, mas a lib exige
    base_url=AI_KERNEL_URL
) if AI_KERNEL_URL else None

"""
Serviço de Inteligência Artificial híbrido para atendimentos.
- Resumo executivo via API compatível OpenAI (ex.: Groq)
- Embeddings via kernel local (AI_KERNEL_URL) - padrão OpenAI /embeddings
- Busca semântica com similaridade de cosseno
"""


MAX_CHARS_EMBED = 6000


def _truncar_texto_para_embedding(text: str, max_chars: int = MAX_CHARS_EMBED) -> str:
    """
    Trunca o texto para o limite de caracteres, evitando corte no meio de palavra.
    """
    if not text or len(text) <= max_chars:
        return text
    cortado = text[:max_chars]
    ultimo_espaco = cortado.rfind(' ')
    if ultimo_espaco > max_chars // 2:
        return cortado[:ultimo_espaco]
    return cortado


def _chamar_ollama_embed(text: str) -> Optional[List[float]]:
    """Gera o vetor batendo no endpoint local que exige o campo 'texts'."""
    if not text:
        return None

    url = f"{AI_KERNEL_URL.rstrip('/')}/embeddings"

    # Payload ajustado conforme o erro 422: exige a chave 'texts'
    payload = {
        "model": AI_KERNEL_EMBEDDING_MODEL,
        "texts": [text]
    }

    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        # Tenta extrair o vetor nos formatos mais comuns de resposta desses kernels
        if "data" in data and len(data["data"]) > 0:
            item = data["data"][0]
            return item["embedding"] if "embedding" in item else item
        elif "embeddings" in data and len(data["embeddings"]) > 0:
            return data["embeddings"][0]

        print(f"[IA EMBED ERROR] Formato de resposta desconhecido: {data.keys()}")
        return None

    except Exception as e:
        print(f"[IA EMBED ERROR] Falha ao gerar vetor: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"[IA EMBED ERROR DETALHES] {e.response.text}")
        return None


def _limpar_json_markdown(raw) -> str:
    """
    Remove blocos de markdown (```json ... ``` ou ``` ... ```) da resposta.
    Retorna a string JSON limpa. Aceita list (converte para string).
    """
    if raw is None:
        return ""
    if isinstance(raw, list):
        raw = " ".join(str(x) for x in raw)
    if not isinstance(raw, str):
        return str(raw)
    s = raw.strip()
    # Remove todos blocos ```json ... ``` OU ``` ... ``` (pode haver múltiplos)
    s = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", lambda m: m.group(1).strip(), s, flags=re.MULTILINE)
    # Remove linhas isoladas começando/terminando com ```
    s = re.sub(r"^```(?:json)?\s*$", "", s, flags=re.MULTILINE)
    s = re.sub(r"^```\s*$", "", s, flags=re.MULTILINE)
    return s.strip()


def _to_stripped_str(val: Any) -> str:
    """Converte valor para string e aplica strip. Aceita list, None, etc."""
    if val is None:
        return ""
    if isinstance(val, list):
        return " ".join(str(x) for x in val).strip()
    if not isinstance(val, str):
        return str(val).strip()
    return (val or "").strip()


def _parsear_resumo_json(raw: str) -> Optional[Dict[str, str]]:
    """
    Tenta parsear a resposta como JSON com chaves situacao_atual, providencias, pendencias.
    Retorna dicionário ou None.
    Protege contra respostas lixo muito curtas.
    """
    try:
        limpo = _limpar_json_markdown(raw)
        
        # Validação 1: JSON limpo muito curto
        if limpo is None or len(limpo.strip()) < 20:
            print(f"AVISO: Resposta IA muito curta/inválida (markdown limpo <20 chars): {repr(limpo)}")
            return None
            
        obj = json.loads(limpo)
        if isinstance(obj, dict):
            # Monta texto_final para validação semântica
            situacao = _to_stripped_str(obj.get("situacao_atual"))
            providencias = _to_stripped_str(obj.get("providencias"))
            texto_final = f"{situacao} {providencias}".strip()
            
            # Validação 2: Conteúdo real muito curto (Vacina Anti-Ponto)
            if len(texto_final) < 30:
                print(f"AVISO: Resposta IA rejeitada por conteúdo insuficiente (<30 chars): {texto_final!r}")
                return None
            return obj
            
    except (json.JSONDecodeError, TypeError):
        print("AVISO: Erro ao parsear resultado IA como JSON válido.")
        pass
    return None


def _chamar_llm_generate(prompt: str, system: Optional[str] = None) -> Optional[str]:
    """Chama a API (Groq/OpenAI) para geração de texto."""
    if not llm_client:
        logger.error("Cliente LLM não configurado.")
        return None

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        response = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.exception("Erro no LLM: %s", e)
        return None


def _texto_despatches(tramitacoes) -> str:
    """Monta texto de despachos a partir do queryset de Tramitacao."""
    partes = []
    for t in tramitacoes.order_by('data_tramitacao'):
        despacho = (t.despacho or "").strip()
        if despacho:
            data_str = t.data_tramitacao.strftime('%d/%m/%Y %H:%M') if t.data_tramitacao else ""
            partes.append(f"[{data_str}] {despacho}")
    return "\n\n".join(partes) if partes else ""


def gerar_resumo_atendimento(atendimento) -> Optional[str]:
    """
    Gera resumo executivo do atendimento via LLM (API compatível OpenAI).
    Retorna o texto do resumo formatado ou None em caso de erro.
    """
    from ..models import Tramitacao

    titulo = (atendimento.titulo or "").strip()
    descricao = (atendimento.descricao or "").strip()
    resumo_atual = (atendimento.resumo_ia_local or "").strip()

    tramitacoes = Tramitacao.objects.filter(atendimento=atendimento)
    texto_tramitacoes = _texto_despatches(tramitacoes)

    if resumo_atual and texto_tramitacoes:
        contexto = f"""RESUMO ANTERIOR:\n{resumo_atual}\n\nNOVAS TRAMITAÇÕES (despachos):\n{texto_tramitacoes}"""
    elif resumo_atual:
        contexto = f"""RESUMO ANTERIOR:\n{resumo_atual}\n\n(Sem tramitações novas.)"""
    else:
        triagem = f"""TÍTULO: {titulo}\n\nDESCRIÇÃO INICIAL (triagem):\n{descricao}"""
        contexto = triagem
        if texto_tramitacoes:
            contexto += f"""\n\nTRAMITAÇÕES:\n{texto_tramitacoes}"""

    system_prompt = (
        "Você é um assistente administrativo especializado em gestão de gabinetes públicos. "
        "Sua tarefa é analisar atendimentos e produzir resumos executivos em português brasileiro. "
        "Responda ESTRITAMENTE com um JSON válido contendo as chaves: "
        '"situacao_atual", "providencias", "pendencias". '
        "Use linguagem objetiva e formal. Não invente informações. "
        "Cada chave deve conter texto em um único parágrafo."
    )

    prompt = f"""Analise o conteúdo abaixo de um atendimento de gabinete e retorne um JSON com as chaves:
- situacao_atual: descrição objetiva da situação atual
- providencias: providências já tomadas
- pendencias: pendências, se houver (ou string vazia "")

CONTEÚDO:
{contexto}

JSON:"""

    resultado_raw = None
    try:
        resultado_raw = _chamar_llm_generate(prompt, system=system_prompt)
    except Exception as e:
        logger.exception("gerar_resumo_atendimento: erro inesperado - %s", e)
        return None

    if resultado_raw is None:
        return None

    # Normalização
    if isinstance(resultado_raw, list):
        resultado_raw = "".join(str(x) for x in resultado_raw)
    elif not isinstance(resultado_raw, str):
        resultado_raw = str(resultado_raw)
    resultado_raw = (resultado_raw or "").strip()

    # Validação preliminar
    if len(resultado_raw) < 20:
        print(f"AVISO: Resposta IA muito curta/inválida (raw <20 chars): {repr(resultado_raw)}")
        return None

    # Tenta parsear JSON
    parsed = _parsear_resumo_json(resultado_raw)
    if parsed:
        partes = []
        situacao = _to_stripped_str(parsed.get("situacao_atual"))
        providencias = _to_stripped_str(parsed.get("providencias"))
        pendencias = _to_stripped_str(parsed.get("pendencias"))
        if situacao:
            partes.append(f"Situação Atual: {situacao}")
        if providencias:
            partes.append(f"Providências Tomadas: {providencias}")
        if pendencias:
            partes.append(f"Pendências: {pendencias}")
        if partes:
            out = "\n\n".join(partes)
            return out

    # Fallback (modelo não retornou JSON válido)
    # Validação final do fallback
    if len(resultado_raw.strip()) < 30:
        print(f"AVISO: Resposta IA (fallback) rejeitada por ser muito curta: {repr(resultado_raw)}")
        return None
        
    return resultado_raw


def _construir_texto_para_embedding(atendimento) -> str:
    """
    Monta o texto rico para embedding (Atendimentos), incluindo metadados completos,
    pessoas envolvidas, descrição e histórico detalhado de tramitações.
    """
    partes = []

    # 1. Cabeçalho e Metadados
    partes.append(f"Protocolo: {atendimento.protocolo}")
    if atendimento.data_criacao:
        partes.append(f"Data: {atendimento.data_criacao.strftime('%d/%m/%Y')}")

    status_txt = atendimento.get_status_display() if hasattr(atendimento, 'get_status_display') else atendimento.status
    partes.append(f"Status Atual: {status_txt}")

    if atendimento.origem:
        origem_txt = atendimento.get_origem_display() if hasattr(atendimento, 'get_origem_display') else atendimento.origem
        partes.append(f"Origem: {origem_txt}")

    # 2. Pessoas Envolvidas
    if atendimento.municipe:
        partes.append(f"Munícipe: {atendimento.municipe.nome_completo}")
        if atendimento.municipe.endereco and isinstance(atendimento.municipe.endereco, dict):
            bairro = atendimento.municipe.endereco.get('bairro') or atendimento.municipe.endereco.get('bairro_nome')
            if bairro:
                partes.append(f"Bairro do Munícipe: {bairro}")

    if atendimento.responsavel:
        resp_nome = getattr(atendimento.responsavel, 'get_full_name', lambda: atendimento.responsavel.username)() or atendimento.responsavel.username
        partes.append(f"Responsável Atual: {resp_nome}")

    if atendimento.conta:
        partes.append(f"Gabinete: {atendimento.conta.nome}")

    # 3. Conteúdo Principal
    assunto = (atendimento.titulo or "").strip()
    descricao = (atendimento.descricao or "").strip()
    partes.append(f"Assunto: {assunto}")
    partes.append(f"Descrição Original: {descricao}")

    # 4. Inteligência (Resumo existente)
    resumo = (atendimento.resumo_ia_local or "").strip()
    if resumo:
        partes.append(f"Resumo Técnico (IA): {resumo}")

    # 5. Histórico de Tramitações (Detalhado)
    tramitacoes = atendimento.tramitacoes.all().order_by('data_tramitacao')
    if tramitacoes.exists():
        hist = []
        for t in tramitacoes:
            data_t = t.data_tramitacao.strftime('%d/%m/%y') if t.data_tramitacao else ""
            usuario_t = t.usuario.username if t.usuario else "Sistema"
            trecho = f"[{data_t} - {usuario_t}]"
            if t.status_anterior and t.status_novo and t.status_anterior != t.status_novo:
                trecho += f" Mudou status ({t.status_anterior} -> {t.status_novo})."
            if t.encaminhado_para_nome:
                trecho += f" Encaminhou para: {t.encaminhado_para_nome}."
            despacho = (t.despacho or "").strip()
            if despacho:
                trecho += f" Nota: {despacho}"
            hist.append(trecho)
        partes.append("Histórico de Andamento: " + " | ".join(hist))

    # 6. Anexos (Apenas nomes para contexto)
    if hasattr(atendimento, 'anexos'):
        anexos_list = atendimento.anexos.all()
        if anexos_list.exists():
            nomes = [a.descricao or str(a.arquivo).split('/')[-1] for a in anexos_list]
            partes.append(f"Arquivos Anexos: {', '.join(nomes)}")

    texto = "\n".join(partes)
    if not texto.strip():
        texto = "Assunto: (sem título) | Descrição: (sem descrição)"
    return texto


def atualizar_vetor_atendimento(atendimento) -> bool:
    """Gera o embedding do atendimento e salva."""
    texto = _construir_texto_para_embedding(atendimento)
    
    # Vacina: não gasta processamento com texto vazio
    if len(texto) < 10:
        return False
        
    vetor = _chamar_ollama_embed(texto)
    if vetor is None:
        return False

    atendimento.vetor_ia_atendimento = vetor
    atendimento.save(update_fields=['vetor_ia_atendimento'])
    return True


def gerar_texto_perfil_municipe(municipe) -> str:
    """
    Cria uma string rica com DADOS CADASTRAIS + INTERAÇÕES RECENTES para o Munícipe.
    Tenta replicar a visão do 'Dossiê' do frontend.
    """
    def _s(v):
        return (v or "").strip() if v is not None else ""

    partes = []

    # 1. Identificação Pessoal
    nome = _s(municipe.nome_completo)
    apelido = _s(municipe.nome_de_guerra)
    partes.append(f"Nome: {nome}")
    if apelido:
        partes.append(f"Apelido: {apelido}")

    if municipe.cpf:
        partes.append(f"CPF: {municipe.cpf}")

    if municipe.data_nascimento:
        try:
            hoje = date.today()
            delta = (hoje - municipe.data_nascimento).days
            idade = max(0, delta // 365)
            partes.append(f"Idade: {idade} anos")
        except (TypeError, ValueError):
            pass

    # 2. Contatos (Crucial para CRM)
    if municipe.telefones:
        tels = []
        for t in municipe.telefones:
            val = t.get('numero') if isinstance(t, dict) else str(t)
            if val:
                tels.append(val)
        if tels:
            partes.append(f"Telefones: {', '.join(tels)}")

    if municipe.emails:
        ems = []
        for e in municipe.emails:
            val = e.get('email') if isinstance(e, dict) else str(e)
            if val:
                ems.append(val)
        if ems:
            partes.append(f"Emails: {', '.join(ems)}")

    # 3. Profissional e Influência
    cargos_str = []
    if _s(municipe.cargo):
        cargos_str.append(municipe.cargo)
    if _s(municipe.orgao):
        cargos_str.append(f"no órgão {municipe.orgao}")
    for p in municipe.perfis.select_related('conta', 'categoria').all():
        detalhe = [x for x in [p.cargo, p.instituicao] if x]
        if detalhe:
            conta_nome = p.conta.nome if p.conta else ""
            cargos_str.append(f"{' na '.join(detalhe)} ({conta_nome})" if conta_nome else ' na '.join(detalhe))

    if cargos_str:
        partes.append(f"Ocupação/Vínculos: {', '.join(cargos_str)}")

    categorias = [p.categoria.nome for p in municipe.perfis.select_related('categoria') if p.categoria]
    categoria = ", ".join(sorted(set(categorias))) if categorias else ""
    if categoria:
        partes.append(f"Categoria: {categoria}")

    # 4. Geografia (Endereço completo)
    end = municipe.endereco or {}
    endereco_parts = []
    if end.get("logradouro"):
        endereco_parts.append(end.get("logradouro"))
    if end.get("bairro") or end.get("bairro_nome"):
        endereco_parts.append(f"Bairro {end.get('bairro') or end.get('bairro_nome')}")
    if end.get("cidade") or end.get("municipio") or end.get("localidade"):
        endereco_parts.append(end.get("cidade") or end.get("municipio") or end.get("localidade"))
    if endereco_parts:
        partes.append(f"Endereço: {', '.join(endereco_parts)}")

    # 5. Observações e Etiquetas
    obs = _s(municipe.observacoes)
    if obs:
        partes.append(f"Observações: {obs}")
    tags = _s(municipe.dados_etiqueta)
    if tags:
        partes.append(f"Etiquetas: {tags}")

    # --- SESSÃO INTERAÇÕES ---
    try:
        ultimos_atendimentos = municipe.atendimentos.all().order_by('-data_criacao')[:5]
        if ultimos_atendimentos:
            resumos_at = []
            for a in ultimos_atendimentos:
                dt = a.data_criacao.strftime('%d/%m/%y') if a.data_criacao else "?"
                st = a.status or "Status?"
                tit = (a.titulo or "")[:80]
                resumos_at.append(f"[{dt}] {tit} ({st})")
            partes.append("Últimos Atendimentos: " + " | ".join(resumos_at))
    except AttributeError:
        try:
            ultimos_atendimentos = municipe.atendimento_set.all().order_by('-data_criacao')[:5]
            if ultimos_atendimentos:
                resumos_at = []
                for a in ultimos_atendimentos:
                    if a.data_criacao:
                        resumos_at.append(f"[{a.data_criacao.strftime('%d/%m/%y')}] {a.titulo or ''}")
                if resumos_at:
                    partes.append("Últimos Atendimentos: " + " | ".join(resumos_at))
        except AttributeError:
            pass

    texto_final = "\n".join(partes)
    if len(texto_final.strip()) < 30:
        return ""

    return texto_final


def atualizar_vetor_municipe(municipe) -> bool:
    """Gera o texto do perfil, o embedding via Ollama e persiste."""
    texto = gerar_texto_perfil_municipe(municipe)
    
    # Vacina
    if not texto or len(texto.strip()) < 30:
        return False
        
    vetor = _chamar_ollama_embed(texto)
    if vetor is None:
        return False

    from django.utils import timezone
    municipe.perfil_ia_texto = texto
    municipe.vetor_ia_perfil = vetor
    municipe.auditoria_ia_data = timezone.now()
    municipe.save(update_fields=['perfil_ia_texto', 'vetor_ia_perfil', 'auditoria_ia_data'])
    return True


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Similaridade de cosseno entre dois vetores."""
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _encontrar_melhor_snippet(query_vec, titulo, descricao, tramitacoes, embed_fn) -> tuple:
    """Retorna (tipo, snippet) do melhor match."""
    tipo = 'TRIAGEM'
    snippet = ""
    melhor_score = -1.0

    txt_triagem = f"ASSUNTO: {titulo}\n\n{descricao}".strip()
    if txt_triagem and len(txt_triagem) >= 20:
        vec = embed_fn(txt_triagem)
        if vec is not None:
            v = np.array(vec, dtype=np.float64)
            sim = _cosine_similarity(query_vec, v)
            if sim > melhor_score:
                melhor_score = sim
                tipo = 'TRIAGEM'
                snippet = (descricao or titulo)[:200]
                if len((descricao or titulo)) > 200:
                    snippet += "..."

    for t in (tramitacoes or []):
        despacho = (t.despacho or "").strip()
        if not despacho:
            continue
        vec = embed_fn(despacho)
        if vec is None:
            continue
        v = np.array(vec, dtype=np.float64)
        sim = _cosine_similarity(query_vec, v)
        if sim > melhor_score:
            melhor_score = sim
            tipo = 'TRAMITACAO'
            snippet = despacho[:200]
            if len(despacho) > 200:
                snippet += "..."

    return (tipo, snippet)


def buscar_atendimentos_semantico(query: str, conta_id: int, top_k: int = 10) -> List[Dict[str, Any]]:
    """Busca semântica padrão (com pgvector se disponível no modelo, aqui simulado via loop se necessário)."""
    from ..models import Atendimento

    query = (query or "").strip()
    if not query:
        return []

    query_vec = _chamar_ollama_embed(query)
    if query_vec is None:
        return []

    qv = np.array(query_vec, dtype=np.float64)

    atendimentos = (
        Atendimento.objects
        .filter(conta_id=conta_id, vetor_ia_atendimento__isnull=False)
        .exclude(vetor_ia_atendimento=[])
        .select_related('municipe', 'conta')
        .prefetch_related('tramitacoes')
    )

    resultados = []
    for a in atendimentos:
        vetor = a.vetor_ia_atendimento
        if not vetor:
            continue
        av = np.array(vetor, dtype=np.float64)
        score = _cosine_similarity(qv, av)

        match_tipo, snippet = _encontrar_melhor_snippet(
            qv, a.titulo or "", a.descricao or "", list(a.tramitacoes.all()), _chamar_ollama_embed
        )
        if match_tipo == 'TRIAGEM':
             desc = (a.descricao or "")[:200]
             if len(a.descricao or "") > 200: desc += "..."
             snippet = desc

        resultados.append({
            "atendimento_id": a.id,
            "protocolo": a.protocolo,
            "titulo": a.titulo,
            "nome_municipe": a.municipe.nome_completo if a.municipe else "",
            "conta_nome": a.conta.nome if a.conta else "",
            "score": round(float(score), 4),
            "match_tipo": match_tipo,
            "snippet": snippet,
        })

    resultados.sort(key=lambda x: x["score"], reverse=True)
    return resultados[:top_k]


def buscar_atendimentos_semantico_otimizado(
    query: str,
    conta_id: Optional[int] = None,
    top_k: int = 10,
    threshold: float = 0.55,
) -> List[Dict[str, Any]]:
    """Versão otimizada com NumPy para busca vetorial."""
    from ..models import Atendimento

    query = (query or "").strip()
    if not query:
        return []

    query_vec = _chamar_ollama_embed(query)
    if query_vec is None:
        return []

    qv = np.array(query_vec, dtype=np.float64)
    qv_norm = np.linalg.norm(qv)
    if qv_norm == 0:
        return []

    filtro = dict(vetor_ia_atendimento__isnull=False)
    if conta_id is not None:
        filtro["conta_id"] = conta_id
    id_vetores = list(
        Atendimento.objects.filter(**filtro)
        .exclude(vetor_ia_atendimento=[])
        .values_list("id", "vetor_ia_atendimento")
    )

    if not id_vetores:
        return []

    ids = [row[0] for row in id_vetores]
    vetores = [row[1] for row in id_vetores]

    try:
        matriz = np.array(vetores, dtype=np.float64)
    except (ValueError, TypeError):
        return []

    dots = np.dot(matriz, qv)
    norms = np.linalg.norm(matriz, axis=1)
    denom = norms * qv_norm
    denom = np.where(denom == 0, 1.0, denom)
    scores = np.divide(dots, denom)

    mask = scores >= threshold
    indices = np.where(mask)[0]
    if indices.size == 0:
        return []

    idx_scores = list(zip(indices, scores[indices]))
    idx_scores.sort(key=lambda x: x[1], reverse=True)
    top_indices = [idx_scores[i][0] for i in range(min(top_k, len(idx_scores)))]

    ids_vencedores = [ids[i] for i in top_indices]
    scores_vencedores = [float(scores[i]) for i in top_indices]

    atendimentos = (
        Atendimento.objects.filter(id__in=ids_vencedores)
        .select_related("municipe", "conta")
        .in_bulk()
    )

    resultados = []
    for aid, score in zip(ids_vencedores, scores_vencedores):
        a = atendimentos.get(aid)
        if a is None:
            continue
        snippet = (a.descricao or a.titulo or "")[:200]
        if len(a.descricao or a.titulo or "") > 200:
            snippet += "..."

        resultados.append({
            "atendimento": a,
            "score": round(score, 4),
            "score_percentual": round(score * 100, 2),
            "snippet": snippet,
        })

    return resultados


def buscar_municipes_semantico(
    query: str,
    limite: int = 20,
    threshold: float = 0.5,
) -> List[Dict[str, Any]]:
    """Busca semântica de munícipes turbinada com Query Expansion (LLM) e Prefixo Mxbai."""
    from ..models import Municipe

    query = (query or "").strip()
    if not query:
        return []

    # 1. QUERY EXPANSION VIA LLM (Groq)
    # Pede para o Llama expandir a busca com sinônimos úteis (ex: liderança -> presidente de bairro, associação)
    system_prompt = (
        "Você é um especialista em banco de dados de CRM governamental. "
        "Sua tarefa é expandir a busca do usuário com sinônimos profissionais para melhorar a busca vetorial. "
        "Não responda com JSON. Responda APENAS com a nova string de busca expandida. "
        "Mantenha nomes próprios, bairros e cidades intactos. "
        "Exemplo: se o usuário digitar 'lideranças jundiapeba', retorne 'liderança, presidente de associação, líder comunitário, representante de bairro, jundiapeba'."
    )
    
    prompt_expansao = f"Expanda esta busca adicionando sinônimos de cargos ou funções, mantendo o local:\nBusca: {query}"
    
    query_expandida = _chamar_llm_generate(prompt_expansao, system=system_prompt)
    
    # Se o LLM falhar ou não retornar nada, faz fallback para a query original
    if not query_expandida or len(query_expandida) < 3:
        query_expandida = query
    else:
        # Limpa possível formatação indesejada do LLM
        query_expandida = query_expandida.replace('\"', '').replace('\n', ' ').strip()
        logger.info(f"[IA SEARCH] Query original: '{query}' | Expandida: '{query_expandida}'")

    # 2. APLICA O PREFIXO OBRIGATÓRIO DO MXBAI PARA BUSCAS (Queries)
    # O modelo mxbai-embed-large exige este prefixo exato para vetorizar perguntas/buscas corretamente
    query_formatada_para_vetor = f\"Represent this sentence for searching relevant passages: {query_expandida}\"

    # 3. GERA O VETOR DA BUSCA EXPANDIDA E FORMATADA
    query_vec = _chamar_ollama_embed(query_formatada_para_vetor)
    if query_vec is None:
        return []

    qv = np.array(query_vec, dtype=np.float64)
    qv_norm = np.linalg.norm(qv)
    if qv_norm == 0:
        return []

    id_vetores = list(
        Municipe.objects
        .filter(vetor_ia_perfil__isnull=False)
        .exclude(vetor_ia_perfil=[])
        .values_list("id", "vetor_ia_perfil")
    )

    if not id_vetores:
        return []

    ids = [row[0] for row in id_vetores]
    vetores = [row[1] for row in id_vetores]

    try:
        matriz = np.array(vetores, dtype=np.float64)
    except (ValueError, TypeError):
        return []

    dots = np.dot(matriz, qv)
    norms = np.linalg.norm(matriz, axis=1)
    denom = norms * qv_norm
    denom = np.where(denom == 0, 1.0, denom)
    scores = np.divide(dots, denom)

    mask = scores >= threshold
    indices = np.where(mask)[0]
    if indices.size == 0:
        return []

    idx_scores = list(zip(indices, scores[indices]))
    idx_scores.sort(key=lambda x: x[1], reverse=True)
    top_indices = [idx_scores[i][0] for i in range(min(limite, len(idx_scores)))]

    ids_vencedores = [ids[i] for i in top_indices]
    scores_vencedores = [float(scores[i]) for i in top_indices]

    municipes = Municipe.objects.filter(id__in=ids_vencedores).prefetch_related("perfis").in_bulk()

    resultados = []
    for mid, score in zip(ids_vencedores, scores_vencedores):
        m = municipes.get(mid)
        if m is None:
            continue
        resultados.append({
            "municipe": m,
            "score": round(score, 4),
            "score_percentual": round(score * 100, 2),
        })

    return resultados