"""
Serviço de Inteligência Artificial local (Ollama) para atendimentos.
- Resumo executivo via DeepSeek
- Embeddings via mxbai-embed-large
- Busca semântica com similaridade de cosseno
"""
import json
from datetime import date
import re
import logging
from typing import List, Dict, Optional, Any, Tuple
from urllib.parse import urljoin

import numpy as np
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Configurações defensivas com fallback
OLLAMA_BASE_URL = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434') or 'http://localhost:11434'
OLLAMA_MODEL_TEXT = getattr(settings, 'OLLAMA_MODEL_TEXT', 'deepseek') or 'deepseek'
OLLAMA_MODEL_EMBED = getattr(settings, 'OLLAMA_MODEL_EMBED', 'mxbai-embed-large') or 'mxbai-embed-large'
OLLAMA_TIMEOUT_GENERATE = getattr(settings, 'OLLAMA_TIMEOUT_GENERATE', (5, 60))  # (connect, read)
OLLAMA_TIMEOUT_EMBED = getattr(settings, 'OLLAMA_TIMEOUT_EMBED', 30)
OLLAMA_TIMEOUT = getattr(settings, 'OLLAMA_TIMEOUT', 120)  # fallback legado


def _ollama_url(path: str) -> str:
    """Monta URL do Ollama evitando barras duplas."""
    base = (OLLAMA_BASE_URL or '').rstrip('/')
    return urljoin(f"{base}/", path.lstrip('/'))


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
    """
    Gera embedding do texto via Ollama (mxbai-embed-large).
    Retorna lista de floats ou None em caso de erro.
    Timeout de 30 segundos.
    Textos acima de 6000 caracteres são truncados silenciosamente para evitar 400 (token limit).
    """
    if not text or not isinstance(text, str):
        logger.warning("gerar_embedding: texto vazio ou inválido")
        return None

    text = _truncar_texto_para_embedding(text)

    url = _ollama_url('/api/embed')
    payload = {"model": OLLAMA_MODEL_EMBED, "input": text}
    timeout = OLLAMA_TIMEOUT_EMBED if isinstance(OLLAMA_TIMEOUT_EMBED, (int, float)) else 30

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        embs = data.get("embeddings") or []
        return embs[0] if embs else None
    except requests.exceptions.Timeout as e:
        logger.error("gerar_embedding: timeout após %s segundos - %s", timeout, e)
        return None
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            model = payload.get("model", "?")
            logger.error("Ollama 404: modelo '%s' não encontrado. Execute: ollama pull %s", model, model)
        logger.exception("gerar_embedding: erro HTTP - %s", e)
        return None
    except requests.RequestException as e:
        logger.exception("gerar_embedding: erro na requisição - %s", e)
        return None
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.exception("gerar_embedding: erro ao processar resposta - %s", e)
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
    # Remove ```json ... ``` ou ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s)
    if match:
        return match.group(1).strip()
    return s


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
    """
    try:
        limpo = _limpar_json_markdown(raw)
        obj = json.loads(limpo)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _chamar_ollama_generate(
    prompt: str,
    system: Optional[str] = None,
    timeout: Optional[Tuple[int, int]] = None,
) -> Optional[str]:
    """
    Chama o endpoint /api/generate do Ollama para gerar texto.
    Retorna o texto gerado ou None em caso de erro.
    Timeout: (5s conexão, 60s leitura).
    """
    url = _ollama_url('/api/generate')
    tout = timeout or OLLAMA_TIMEOUT_GENERATE
    if isinstance(tout, (tuple, list)) and len(tout) >= 2:
        connect_timeout, read_timeout = tout[0], tout[1]
    else:
        connect_timeout = read_timeout = tout if isinstance(tout, (int, float)) else 60

    payload = {
        "model": OLLAMA_MODEL_TEXT,
        "prompt": prompt,
        "stream": False,
    }
    if system:
        payload["system"] = system

    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=(connect_timeout, read_timeout),
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("response")
        if raw is None:
            return ""
        if isinstance(raw, list):
            raw = "".join(str(x) for x in raw) if raw else ""
        elif not isinstance(raw, str):
            raw = str(raw)
        return (raw or "").strip()
    except requests.exceptions.Timeout as e:
        logger.error(
            "Ollama generate: timeout (connect=%s, read=%s) - %s",
            connect_timeout,
            read_timeout,
            e,
        )
        return None
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            model = payload.get("model", "?")
            logger.error("Ollama 404: modelo '%s' não encontrado. Execute: ollama pull %s", model, model)
        logger.exception("Ollama generate: erro HTTP - %s", e)
        return None
    except requests.RequestException as e:
        logger.exception("Ollama generate: erro na requisição - %s", e)
        return None
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.exception("Ollama generate: erro ao processar resposta - %s", e)
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
    Gera resumo executivo do atendimento via DeepSeek.
    Se houver resumo_ia_local, envia ele + apenas novas tramitações.
    Caso contrário, envia triagem (descricao) + todas as tramitações.

    Retorna o texto do resumo formatado ou None em caso de erro.
    A IA é instruída a retornar JSON com chaves: situacao_atual, providencias, pendencias.
    """
    from ..models import Tramitacao

    titulo = (atendimento.titulo or "").strip()
    descricao = (atendimento.descricao or "").strip()
    resumo_atual = (atendimento.resumo_ia_local or "").strip()

    tramitacoes = Tramitacao.objects.filter(atendimento=atendimento)
    texto_tramitacoes = _texto_despatches(tramitacoes)

    if resumo_atual and texto_tramitacoes:
        contexto = f"""RESUMO ANTERIOR:
{resumo_atual}

NOVAS TRAMITAÇÕES (despachos):
{texto_tramitacoes}"""
    elif resumo_atual:
        contexto = f"""RESUMO ANTERIOR:
{resumo_atual}

(Sem tramitações novas.)"""
    else:
        triagem = f"""TÍTULO: {titulo}

DESCRIÇÃO INICIAL (triagem):
{descricao}"""
        contexto = triagem
        if texto_tramitacoes:
            contexto += f"""

TRAMITAÇÕES:
{texto_tramitacoes}"""

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
        resultado_raw = _chamar_ollama_generate(prompt, system=system_prompt)
    except Exception as e:
        logger.exception("gerar_resumo_atendimento: erro inesperado - %s", e)
        return None

    if resultado_raw is None:
        return None

    # Normaliza: API pode retornar lista em vez de string
    if isinstance(resultado_raw, list):
        resultado_raw = (
            "".join(str(x) for x in resultado_raw)
            if all(isinstance(x, str) for x in resultado_raw)
            else " ".join(str(x) for x in resultado_raw)
        )
    elif not isinstance(resultado_raw, str):
        resultado_raw = str(resultado_raw)
    resultado_raw = (resultado_raw or "").strip()

    if not resultado_raw:
        return None

    # Tenta parsear como JSON estruturado
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
            print(f"DEBUG IA RAW RESPONSE: {type(resultado_raw)} - {resultado_raw[:200]!r}... -> parsed OK")
            return out

    # Fallback: retorna texto bruto (modelo não retornou JSON válido)
    preview = (resultado_raw[:500] + "...") if len(resultado_raw) > 500 else resultado_raw
    print(f"DEBUG IA RAW RESPONSE: {type(resultado_raw)} - {preview!r}")
    return resultado_raw


def _construir_texto_para_embedding(atendimento) -> str:
    """
    Monta o texto rico para geração do embedding, incluindo assunto, descrição,
    resumo IA, despachos das tramitações e status quando encaminhado.
    """
    assunto = (atendimento.titulo or "").strip()
    descricao = (atendimento.descricao or "").strip()
    partes = [f"Assunto: {assunto}", f"Descrição: {descricao}"]
    texto = " | ".join(partes)

    resumo = (atendimento.resumo_ia_local or "").strip()
    if resumo:
        texto += f" | Resumo Técnico: {resumo}"

    tramitacoes = atendimento.tramitacoes.all().order_by('data_tramitacao')
    if tramitacoes:
        despachos = []
        for t in tramitacoes:
            despacho = (t.despacho or "").strip()
            if despacho:
                despachos.append(despacho)
            if getattr(t, 'encaminhado_para_nome', None):
                nome_destino = (t.encaminhado_para_nome or "").strip()
                if nome_destino:
                    despachos.append(f"Encaminhado para {nome_destino}")
        if despachos:
            texto += " | Histórico: " + " | ".join(despachos)

    if atendimento.status == 'ENCAMINHADO':
        texto += " | Status: Encaminhado"

    if not texto.strip():
        texto = "Assunto: (sem título) | Descrição: (sem descrição)"
    return texto


def atualizar_vetor_atendimento(atendimento) -> bool:
    """
    Gera o embedding do atendimento com texto rico (assunto, descrição, resumo IA,
    tramitações e status) e salva em vetor_ia_atendimento.
    Retorna True se sucesso, False caso contrário.
    """
    texto = _construir_texto_para_embedding(atendimento)
    vetor = _chamar_ollama_embed(texto)
    if vetor is None:
        return False

    atendimento.vetor_ia_atendimento = vetor
    atendimento.save(update_fields=['vetor_ia_atendimento'])
    return True


def gerar_texto_perfil_municipe(municipe) -> str:
    """
    Cria uma string rica com todos os dados relevantes do munícipe para busca semântica.
    Trata campos nulos e inclui idade aproximada quando há data_nascimento.
    """
    def _s(v):
        return (v or "").strip() if v is not None else ""

    nome = _s(municipe.nome_completo)
    apelido = _s(municipe.nome_de_guerra)

    # Profissão/Cargo e Entidade: legado + perfis
    cargos = []
    entidades = []
    if _s(municipe.cargo):
        cargos.append(municipe.cargo)
    if _s(municipe.orgao):
        entidades.append(municipe.orgao)
    for p in municipe.perfis.all():
        if _s(p.cargo):
            cargos.append(p.cargo)
        if _s(p.instituicao):
            entidades.append(p.instituicao)
    profissao = ", ".join(dict.fromkeys(cargos)) if cargos else ""
    nome_entidade = ", ".join(dict.fromkeys(entidades)) if entidades else ""

    categoria = _s(municipe.categoria.nome) if municipe.categoria else ""

    # Endereço (JSONField)
    end = municipe.endereco or {}
    bairro = _s(end.get("bairro") or end.get("bairro_nome"))
    cidade = _s(end.get("cidade") or end.get("municipio") or end.get("localidade"))

    observacao = _s(municipe.observacoes)
    etiquetas = _s(municipe.dados_etiqueta)

    partes = [f"Nome: {nome}"]
    if apelido:
        partes[-1] += f" ({apelido})"
    if profissao:
        partes.append(f"Profissão/Cargo: {profissao}")
    if nome_entidade:
        partes.append(f"Entidade/Empresa: {nome_entidade}")
    if categoria:
        partes.append(f"Categoria: {categoria}")
    if bairro:
        partes.append(f"Bairro: {bairro}")
    if cidade:
        partes.append(f"Cidade: {cidade}")
    if observacao:
        partes.append(f"Observações: {observacao}")
    if etiquetas:
        partes.append(f"Etiquetas: {etiquetas}")

    if municipe.data_nascimento:
        try:
            hoje = date.today()
            delta = (hoje - municipe.data_nascimento).days
            idade = max(0, delta // 365)
            partes.append(f"Idade: {idade} anos")
        except (TypeError, ValueError):
            pass

    texto = " | ".join(partes)
    return texto if texto.strip() else f"Nome: {nome or '(sem nome)'}"


def atualizar_vetor_municipe(municipe) -> bool:
    """
    Gera o texto do perfil, o embedding via Ollama e persiste em perfil_ia_texto,
    vetor_ia_perfil e auditoria_ia_data. Retorna True se sucesso.
    """
    texto = gerar_texto_perfil_municipe(municipe)
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


def _encontrar_melhor_snippet(
    query_vec: np.ndarray,
    titulo: str,
    descricao: str,
    tramitacoes,
    embed_fn,
) -> tuple:
    """
    Identifica se o match foi mais forte na triagem ou em uma tramitação.
    Retorna (tipo, snippet) onde tipo é 'TRIAGEM' ou 'TRAMITACAO'.
    """
    tipo = 'TRIAGEM'
    snippet = ""
    melhor_score = -1.0

    # Triagem: titulo + descricao
    txt_triagem = f"ASSUNTO: {titulo}\n\n{descricao}".strip()
    if txt_triagem:
        vec = embed_fn(txt_triagem)
        if vec is not None:
            v = np.array(vec, dtype=np.float64)
            sim = _cosine_similarity(query_vec, v)
            if sim > melhor_score:
                melhor_score = sim
                tipo = 'TRIAGEM'
                # snippet: primeiros 200 chars da descrição
                snippet = (descricao or titulo)[:200]
                if len((descricao or titulo)) > 200:
                    snippet += "..."

    # Tramitações
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


def buscar_atendimentos_semantico(
    query: str,
    conta_id: int,
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """
    Busca semântica de atendimentos por conta.
    Retorna lista de dicts com: atendimento, score, snippet, match_tipo.
    """
    from ..models import Atendimento, Tramitacao

    query = (query or "").strip()
    if not query:
        return []

    query_vec = _chamar_ollama_embed(query)
    if query_vec is None:
        return []

    qv = np.array(query_vec, dtype=np.float64)

    # Atendimentos da conta com vetor preenchido
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

        # Identificar se match foi em triagem ou tramitação
        tramitacoes = list(a.tramitacoes.all())
        match_tipo, snippet = _encontrar_melhor_snippet(
            qv,
            a.titulo or "",
            a.descricao or "",
            tramitacoes,
            _chamar_ollama_embed,
        )
        # Evitar muitas chamadas ao embed: usar snippet da triagem se for o caso
        if match_tipo == 'TRIAGEM':
            desc = (a.descricao or "")[:200]
            if len(a.descricao or "") > 200:
                desc += "..."
            snippet = desc
        else:
            # Pegar o despacho mais relevante (já calculado)
            pass

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

    # Ordenar por score decrescente e pegar top_k
    resultados.sort(key=lambda x: x["score"], reverse=True)
    return resultados[:top_k]


def buscar_atendimentos_semantico_otimizado(
    query: str,
    conta_id: Optional[int] = None,
    top_k: int = 10,
    threshold: float = 0.55,
) -> List[Dict[str, Any]]:
    """
    Versão otimizada para baixo consumo de RAM (sem pgvector):
    1. Carrega apenas id e vetor_ia_atendimento do banco.
    2. Calcula similaridade em lote via NumPy vetorizado.
    3. Filtra top K acima do threshold.
    4. Só então busca os objetos Atendimento completos dos vencedores.
    Quando conta_id é None, busca em todos os atendimentos (apenas para superuser).
    """
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

    # 1. Busca apenas id e vetor (evita carregar objetos completos)
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

    # 2. Matriz NumPy (n x d)
    try:
        matriz = np.array(vetores, dtype=np.float64)
    except (ValueError, TypeError):
        return []

    # 3. Similaridade de cosseno vetorizada: dot(matrix, qv) / (norm(matrix, axis=1) * norm(qv))
    dots = np.dot(matriz, qv)
    norms = np.linalg.norm(matriz, axis=1)
    denom = norms * qv_norm
    # Evita divisão por zero
    denom = np.where(denom == 0, 1.0, denom)
    scores = np.divide(dots, denom)

    # 4. Top K acima do threshold
    mask = scores >= threshold
    indices = np.where(mask)[0]
    if indices.size == 0:
        return []

    # Ordena por score decrescente e pega top K
    idx_scores = list(zip(indices, scores[indices]))
    idx_scores.sort(key=lambda x: x[1], reverse=True)
    top_indices = [idx_scores[i][0] for i in range(min(top_k, len(idx_scores)))]

    ids_vencedores = [ids[i] for i in top_indices]
    scores_vencedores = [float(scores[i]) for i in top_indices]

    # 5. Busca objetos Atendimento completos apenas dos vencedores
    atendimentos = (
        Atendimento.objects.filter(id__in=ids_vencedores)
        .select_related("municipe", "conta")
        .in_bulk()
    )

    # Mantém ordem por score; retorna atendimento (obj) e score para serialização na view
    resultados = []
    for aid, score in zip(ids_vencedores, scores_vencedores):
        a = atendimentos.get(aid)
        if a is None:
            continue
        txt = (a.titulo or "")[:30]
        print(f"DEBUG BUSCA: ID {aid} - Score: {score:.4f} - Texto: {txt}...")
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
    """
    Busca semântica de munícipes (CRM).
    Usa vetor_ia_perfil; threshold 0.5 para maior precisão em perfis.
    Retorna lista de dicts com: municipe (obj), score, score_percentual.
    """
    from ..models import Municipe

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
