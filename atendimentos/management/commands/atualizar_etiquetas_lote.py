from django.core.management.base import BaseCommand
from atendimentos.models import Municipe
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse


def _parse_int_list(raw_values):
    out = []
    for raw in raw_values or []:
        if raw is None:
            continue
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                continue
    return sorted(set(out))


def _smart_title(texto):
    if not texto:
        return ""
    raw = " ".join(str(texto).strip().split())
    if not raw:
        return ""
    lower_words = {"da", "de", "do", "das", "dos", "e"}
    palavras = raw.lower().split(" ")
    out = []
    for i, p in enumerate(palavras):
        if i > 0 and p in lower_words:
            out.append(p)
            continue
        if len(p) <= 3 and p.isalpha() and p in {"sp", "mg", "rj", "df", "cep"}:
            out.append(p.upper())
            continue
        out.append(p.capitalize())
    return " ".join(out)


def _infer_tratamento(nome, cargo_orgao, tratamento_base):
    if tratamento_base:
        t = str(tratamento_base).strip().lower()
        if "senhora" in t or "sra" in t or "doutora" in t:
            return "Excelentíssima Senhora"
        if "senhor" in t or "sr" in t or "doutor" in t:
            return "Excelentíssimo Senhor"

    texto = f"{nome or ''} {cargo_orgao or ''}".lower()
    femininos = [
        "senhora",
        "sra",
        "sr.ª",
        "srª",
        "secretária",
        "vereadora",
        "deputada",
        "prefeita",
        "presidenta",
        "ministra",
        "diretora",
        "coordenadora",
        "gerente regional",
        "juíza",
        "promotora",
        "defensora",
    ]
    if any(token in texto for token in femininos):
        return "Excelentíssima Senhora"
    primeiro = (nome or "").strip().split(" ")[0].lower()
    if primeiro.endswith("a"):
        return "Excelentíssima Senhora"
    return "Excelentíssimo Senhor"


def _format_endereco(endereco):
    end = endereco if isinstance(endereco, dict) else {}
    logradouro = (end.get("logradouro") or "").strip()
    numero = (end.get("numero") or "").strip()
    bairro = (end.get("bairro") or "").strip()
    cep = (end.get("cep") or "").strip()
    cidade = (end.get("cidade") or "").strip()
    uf = (end.get("uf") or "").strip()

    linha1_base = ", ".join([x for x in [logradouro, numero] if x]).strip(", ")
    if bairro:
        linha1 = " - ".join([x for x in [linha1_base, bairro] if x])
    else:
        linha1 = linha1_base

    cidade_uf = " - ".join([x for x in [cidade, uf] if x])
    if cep:
        linha2 = f"CEP {cep}" + (f" - {cidade_uf}" if cidade_uf else "")
    else:
        linha2 = cidade_uf

    if not linha1:
        linha1 = "Endereço não informado"
    if not linha2:
        linha2 = "CEP não informado"
    return _smart_title(linha1), _smart_title(linha2)


def _fetch_text(url, timeout=8, cache=None):
    if cache is not None and url in cache:
        return cache[url]
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:  # nosec B310
        content = response.read().decode("utf-8", errors="ignore")
        if cache is not None:
            cache[url] = content
        return content


def _strip_html(raw_html):
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_text(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _tokens(text, min_size=3):
    return [t for t in re.split(r"\W+", _normalize_text(text)) if len(t) >= min_size]


def _name_match_score(nome, text):
    name_tokens = _tokens(nome, min_size=3)
    if not name_tokens:
        return 0
    text_norm = _normalize_text(text)
    found = sum(1 for tk in name_tokens if tk in text_norm)
    ratio = found / max(1, len(name_tokens))
    if ratio >= 0.8:
        return 6
    if ratio >= 0.5:
        return 3
    return -4


def _role_hint_score(cargo_orgao, text):
    hints = []
    base = _normalize_text(cargo_orgao)
    if any(k in base for k in ["promotor", "promotora", "ministerio publico", "mpsp"]):
        hints.extend(["promotor", "promotoria", "ministerio publico", "mpsp"])
    if any(k in base for k in ["juiz", "juiza", "judiciario", "tribunal", "vara", "tjsp"]):
        hints.extend(["juiz", "juiza", "vara", "tribunal", "forum", "fórum", "tjsp"])
    if any(k in base for k in ["vereador", "camara", "câmara", "assembleia", "alesp"]):
        hints.extend(["camara", "câmara", "vereador", "assembleia", "alesp"])
    if any(k in base for k in ["prefeito", "prefeita", "prefeitura", "secretaria"]):
        hints.extend(["prefeitura", "secretaria", "gabinete", "paço", "paco"])
    if any(k in base for k in ["defensor", "defensora", "defensoria"]):
        hints.extend(["defensoria", "defensor"])

    text_norm = _normalize_text(text)
    if not hints:
        return 0
    hits = sum(1 for h in set(hints) if h in text_norm)
    if hits >= 3:
        return 4
    if hits >= 1:
        return 2
    return -2


def _domain_score(url, cargo_orgao=""):
    host = (urlparse(url).netloc or "").lower()
    path = (urlparse(url).path or "").lower()
    base = _normalize_text(cargo_orgao)
    score = 0

    if any(x in host for x in ["youtube.com", "instagram.com", "facebook.com", "tiktok.com"]):
        return -8
    if "/w/do-" in path or "imprensaoficial.com.br" in host:
        score -= 7
    if ".gov.br" in host:
        score += 6
    elif ".mp.br" in host or "mpsp" in host:
        score += 7
    elif ".jus.br" in host or "tjsp" in host:
        score += 7
    elif ".org.br" in host or ".com.br" in host:
        score += 3

    if "mpsp" in base or "promotor" in base:
        if "mpsp.mp.br" in host:
            score += 8
    if any(k in base for k in ["judiciario", "juiz", "juiza", "vara", "tribunal"]):
        if ".jus.br" in host or "tjsp" in host:
            score += 8
    if any(k in base for k in ["camara", "câmara", "vereador", "assembleia", "alesp"]):
        if any(k in host for k in ["camara", "al.sp.gov.br", "alesp"]):
            score += 7
    if any(k in base for k in ["prefeitura", "prefeito", "prefeita", "secretaria"]):
        if "prefeitura" in host or ".sp.gov.br" in host:
            score += 6
    return score


def _institutional_query_boost(cargo_orgao, city_hint):
    cargo_norm = _normalize_text(cargo_orgao)
    city = (city_hint or "").strip()
    if not city:
        return []
    queries = []
    if any(k in cargo_norm for k in ["promotor", "promotoria", "ministerio publico", "mpsp"]):
        queries.extend(
            [
                f"site:mpsp.mp.br \"Promotoria de Justiça de {city}\" \"Endereço\"",
                f"site:mpsp.mp.br \"Promotoria de Justiça\" \"{city}\" \"Telefone\" \"E-mail\"",
            ]
        )
    if any(k in cargo_norm for k in ["juiz", "juiza", "vara", "tribunal", "tjsp", "judici"]):
        queries.extend(
            [
                f"site:tjsp.jus.br fórum {city} endereço",
                f"site:tjsp.jus.br comarca de {city} endereço telefone",
            ]
        )
    return queries


def _is_low_quality_source(link):
    u = (link or "").lower()
    return "/w/do-" in u or "imprensaoficial.com.br" in u


def _is_official_source(link):
    host = (urlparse(link).netloc or "").lower()
    if not host:
        return False
    official_suffixes = (".gov.br", ".jus.br", ".mp.br", ".leg.br")
    if host.endswith(official_suffixes):
        return True
    # alguns domínios oficiais recorrentes sem sufixo direto
    if any(x in host for x in ["tjsp.jus.br", "mpsp.mp.br", "camara", "al.sp.gov.br"]):
        return True
    return False


def _mentions_city(text, city):
    city_norm = _normalize_text(city)
    if not city_norm:
        return True
    return city_norm in _normalize_text(text)


def _address_mentions_city(endereco_tuple, city):
    if not city:
        return True
    if not endereco_tuple:
        return False
    joined = " ".join([x for x in endereco_tuple if x])
    return _mentions_city(joined, city)


def _ddg_links(query, limit=6, timeout=8, cache=None):
    if cache is not None and query in cache:
        return cache[query][:limit]
    encoded = urllib.parse.quote_plus(query)
    url = f"https://duckduckgo.com/html/?q={encoded}"
    body = _fetch_text(url, timeout=timeout)
    links = re.findall(r'href="(?:https?:)?//duckduckgo\.com/l/\?[^"]*?uddg=([^"&]+)', body)
    out = []
    for link in links:
        decoded = urllib.parse.unquote(link)
        if decoded not in out:
            out.append(decoded)
        if len(out) >= limit:
            break
    if cache is not None:
        cache[query] = list(out)
    return out


def _ddg_serp_text(query, timeout=8, cache=None):
    key = f"__serp__:{query}"
    if cache is not None and key in cache:
        return cache[key]
    encoded = urllib.parse.quote_plus(query)
    url = f"https://duckduckgo.com/html/?q={encoded}"
    body = _fetch_text(url, timeout=timeout)
    text = _strip_html(body)
    if cache is not None:
        cache[key] = text
    return text


def _serper_links(query, limit=8, timeout=8, api_key="", cache=None):
    key = f"__serper_links__:{query}:{limit}"
    if cache is not None and key in cache:
        return cache[key]
    if not api_key:
        return []
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=json.dumps({"q": query, "num": max(1, int(limit or 1)), "hl": "pt-br", "gl": "br"}).encode("utf-8"),
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "User-Agent": "SIGA-MCP-Hybrid/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:  # nosec B310
        body = response.read().decode("utf-8", errors="ignore")
        payload = json.loads(body or "{}")
    links = []
    for item in payload.get("organic", []) or []:
        link = (item.get("link") or "").strip()
        if link and link not in links:
            links.append(link)
        if len(links) >= limit:
            break
    if cache is not None:
        cache[key] = links
    return links


def _serper_snippet_text(query, timeout=8, api_key="", cache=None):
    key = f"__serper_snippet__:{query}"
    if cache is not None and key in cache:
        return cache[key]
    if not api_key:
        return ""
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=json.dumps({"q": query, "num": 8, "hl": "pt-br", "gl": "br"}).encode("utf-8"),
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "User-Agent": "SIGA-MCP-Hybrid/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:  # nosec B310
        body = response.read().decode("utf-8", errors="ignore")
        payload = json.loads(body or "{}")
    parts = []
    for item in payload.get("organic", []) or []:
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        link = (item.get("link") or "").strip()
        segment = " ".join([x for x in [title, snippet, link] if x])
        if segment:
            parts.append(segment)
    text = " ".join(parts)
    if cache is not None:
        cache[key] = text
    return text


def _rank_links(links, orgao_hint):
    if not links:
        return []
    orgao_tokens = [t for t in re.split(r"\W+", (orgao_hint or "").lower()) if len(t) >= 4]
    noisy_domains = ["youtube.com", "instagram.com", "facebook.com", "tiktok.com"]

    def score(url):
        u = (url or "").lower()
        s = 0
        s += _domain_score(u, orgao_hint)
        if any(d in u for d in noisy_domains):
            s -= 5
        if "contato" in u or "fale-conosco" in u or "institucional" in u:
            s += 2
        for tk in orgao_tokens[:5]:
            if tk in u:
                s += 2
        return s

    return sorted(links, key=score, reverse=True)


def _extract_endereco_from_text(text):
    # Padrão mais confiável: logradouro + cidade/UF + CEP explícito.
    patterns = [
        r"((?:Av\.|Avenida|Rua|R\.|Praça|Travessa|Rodovia|Alameda)\s+.{10,280}?\bCEP[:\s]*\d{5}-?\d{3})",
        r"((?:Endere[cç]o[:\s]+)?(?:Av\.|Avenida|Rua|R\.|Praça|Travessa|Rodovia|Alameda)\s+.{10,260}?\d{5}-?\d{3})",
        r"((?:Av\.|Avenida|Rua|R\.|Praça|Travessa|Rodovia|Alameda)\s+.{10,220}?(?:Mogi das Cruzes|S[ãa]o Paulo|Suzano|Po[áa]|Igarat[áa]|Ferraz de Vasconcelos).{0,60})",
    ]
    trecho = None
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            trecho = re.sub(r"\s+", " ", m.group(1)).strip(" -.,;")
            break
    if not trecho:
        return None
    # Corta ruído comum pós-endereço.
    trecho = re.split(
        r"\b(Telefone|Fax|Horário|Horario|E-?mail|Comiss[aã]o|Funcionamento)\b",
        trecho,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" -.,;|")
    # Remove separadores html/textuais comuns.
    trecho = trecho.replace("|", " - ")
    trecho = re.sub(r"\s*-\s*-\s*", " - ", trecho)
    trecho = re.sub(r"\s+", " ", trecho).strip()

    # Extração específica para endereços da Câmara de Mogi.
    cmmc = re.search(
        r"Av\.\s*Vereador\s*Narciso\s*Yague\s*Guimar[aã]es,\s*381(?:\s*-\s*Centro\s*C[ií]vico)?"
        r".*?CEP[:\s]*0?8780-?902",
        trecho,
        flags=re.IGNORECASE,
    )
    if cmmc:
        return (
            "Av. Vereador Narciso Yague Guimaraes, 381 - Centro Civico",
            "CEP 08780-902 - Mogi das Cruzes - SP",
        )

    # Tenta quebrar em duas linhas no padrão da etiqueta.
    cep_match = re.search(r"\b\d{5}-?\d{3}\b", trecho)
    if cep_match:
        cep = cep_match.group(0)
        before = trecho[: cep_match.start()].strip(" ,;-")
        before = re.sub(r"[, ]*cep$", "", before, flags=re.IGNORECASE).strip(" ,;-")
        after = trecho[cep_match.end() :].strip(" ,;-")
        partes = [p.strip() for p in before.split(" - ") if p.strip()]
        # Linha1 até bairro (sem cidade/UF quando possível)
        linha1 = " - ".join(partes[:2]) if len(partes) >= 2 else before
        linha2 = f"CEP {cep}"
        if after:
            after_clean = re.sub(r"\s+", " ", after).strip(" -.,;")
            # Remove lixo comum remanescente.
            after_clean = re.split(
                r"\b(Telefone|Fax|Horário|Horario|Funcionamento)\b",
                after_clean,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" -.,;|")
            if after_clean:
                linha2 = f"{linha2} - {after_clean}"
        return _smart_title(linha1), _smart_title(linha2)
    # fallback sem CEP explícito, mas com cidade/UF no trecho
    if re.search(r"\b(Mogi das Cruzes|S[ãa]o Paulo|Suzano|Po[áa]|Igarat[áa]|Ferraz de Vasconcelos)\b", trecho, flags=re.IGNORECASE):
        parts = [p.strip() for p in trecho.split(" - ") if p.strip()]
        linha1 = parts[0] if parts else trecho
        linha2 = " - ".join(parts[1:]) if len(parts) > 1 else "CEP não informado"
        return _smart_title(linha1), _smart_title(linha2)
    return None


def _extract_best_address_candidate(nome, cargo_orgao, link, text, preferred_city=""):
    endereco = _extract_endereco_from_text(text)
    if not endereco:
        return None
    if preferred_city and not _address_mentions_city(endereco, preferred_city):
        return None
    score = 0
    score += _domain_score(link, cargo_orgao)
    score += _name_match_score(nome, text)
    score += _role_hint_score(cargo_orgao, text)
    # Penaliza endereço em São Paulo capital quando o contexto parece Mogi/comarca local.
    text_norm = _normalize_text(text)
    cargo_norm = _normalize_text(cargo_orgao)
    if "mogi" in cargo_norm or "comarca" in cargo_norm:
        if "sao paulo" in text_norm and "mogi das cruzes" not in text_norm:
            score -= 4
        if "mogi das cruzes" in text_norm:
            score += 3
    preferred_city_norm = _normalize_text(preferred_city)
    if preferred_city_norm:
        if preferred_city_norm in text_norm:
            score += 8
        else:
            score -= 3
        if "sao paulo" in text_norm and preferred_city_norm != "sao paulo":
            score -= 6
    return {"endereco": endereco, "link": link, "score": score}


def _should_attempt_hybrid(end_l1, end_l2, cargo_orgao, categoria_nome, force=False):
    line1 = _normalize_text(end_l1)
    line2 = _normalize_text(end_l2)
    cargo = _normalize_text(cargo_orgao)
    categoria = _normalize_text(categoria_nome)

    # Caso base: não temos endereço interno minimamente confiável.
    if "nao informado" in line1 or "não informado" in line1:
        return True
    if "cep nao informado" in line2 or "cep não informado" in line2:
        return True

    # Em atualização forçada, permite tentar superar endereço genérico/incompleto.
    if not force:
        return False

    if "judici" in categoria or any(k in cargo for k in ["promotor", "juiz", "defensor", "tribunal", "vara"]):
        # Ex.: somente CEP ou capital sem indicação local costuma gerar baixa qualidade.
        if line2.startswith("cep ") and " - " not in line2:
            return True
        if "sao paulo" in line1 or "sao paulo" in line2:
            return True
    return False


def _buscar_endereco_externo(
    nome,
    cargo_orgao,
    *,
    max_queries=3,
    max_links=4,
    fetch_timeout=5,
    workers=4,
    cache=None,
    web_provider="auto",
    serper_api_key="",
    preferred_city="",
    official_only=False,
    min_score=0,
    return_meta=False,
):
    base_query = f"{nome} {cargo_orgao}".strip()
    orgao_hint = ""
    if " - " in (cargo_orgao or ""):
        # Geralmente o último segmento é o órgão/instituição.
        orgao_hint = (cargo_orgao.split(" - ")[-1] or "").strip()
    elif (cargo_orgao or "").strip():
        orgao_hint = cargo_orgao.strip()

    cidade_hint = (preferred_city or "").strip()
    m_city = re.search(r"\b(Mogi das Cruzes|São Paulo|Igaratá|Suzano|Poá|Biritiba Mirim)\b", base_query, re.IGNORECASE)
    if m_city and not cidade_hint:
        cidade_hint = m_city.group(1)

    queries = [f"{base_query} endereço CEP contato"]
    if cidade_hint:
        queries.append(f"{base_query} {cidade_hint} endereço CEP contato")
    cargo_norm = _normalize_text(cargo_orgao)
    if cidade_hint and any(k in cargo_norm for k in ["promotor", "promotoria", "ministerio publico", "mpsp"]):
        queries.extend(
            [
                f"{nome} promotor de justiça {cidade_hint} endereço",
                f"promotoria de justiça {cidade_hint} endereço CEP",
                f"ministério público {cidade_hint} endereço",
                f"site:mpsp.mp.br promotoria de justiça {cidade_hint} endereço telefone",
            ]
        )
    if cidade_hint and any(k in cargo_norm for k in ["juiz", "juiza", "vara", "tribunal", "tjsp", "judici"]):
        queries.extend(
            [
                f"{nome} {cidade_hint} comarca endereço",
                f"fórum {cidade_hint} endereço CEP",
                f"tribunal de justiça {cidade_hint} endereço",
            ]
        )
    queries.extend(_institutional_query_boost(cargo_orgao, cidade_hint))
    if orgao_hint:
        queries.extend(
            [
                f"{orgao_hint} endereço CEP",
                f"{orgao_hint} contato endereço",
            ]
        )
        if cidade_hint:
            queries.append(f"{orgao_hint} {cidade_hint} endereço CEP")
        queries.append(f"{orgao_hint} sede endereço")

    dedup_queries = []
    for q in queries:
        if q and q not in dedup_queries:
            dedup_queries.append(q)

    query_cache = (cache or {}).setdefault("query_links", {}) if cache is not None else None
    page_cache = (cache or {}).setdefault("page_text", {}) if cache is not None else None
    orgao_cache = (cache or {}).setdefault("orgao_address", {}) if cache is not None else None

    orgao_key = (orgao_hint or "").strip().lower()
    if orgao_cache is not None and orgao_key and orgao_key in orgao_cache:
        cached = orgao_cache[orgao_key]
        if return_meta:
            return cached, {"ok": True, "source": "cache", "score": None}
        return cached

    best_candidate = None
    reject_reasons = []
    for query in dedup_queries[: max(1, int(max_queries or 1))]:
        provider = (web_provider or "auto").strip().lower()
        use_serper = provider in {"serper", "auto"} and bool(serper_api_key)
        # Primeiro tenta extrair do próprio snippet da busca (SERP/API).
        try:
            if use_serper:
                serp_text = _serper_snippet_text(
                    query,
                    timeout=fetch_timeout,
                    api_key=serper_api_key,
                    cache=query_cache,
                )
                serp_link = f"serper:search:{query}"
            else:
                serp_text = _ddg_serp_text(query, timeout=fetch_timeout, cache=query_cache)
                serp_link = f"duckduckgo:serp:{query}"
            serp_candidate = _extract_best_address_candidate(
                nome,
                cargo_orgao,
                link=serp_link,
                text=serp_text,
                preferred_city=cidade_hint,
            )
            if serp_candidate:
                if cidade_hint and not _mentions_city(serp_text, cidade_hint):
                    serp_candidate = None
            if serp_candidate:
                if official_only and not _is_official_source(serp_link):
                    reject_reasons.append(f"serp_non_official:{serp_link}")
                    serp_candidate = None
            if serp_candidate:
                # Confiança moderada para snippet; ainda assim útil quando página final bloqueia.
                serp_candidate["score"] += 2
                if _is_low_quality_source(serp_link):
                    serp_candidate["score"] -= 5
                if best_candidate is None or serp_candidate["score"] > best_candidate["score"]:
                    best_candidate = serp_candidate
        except Exception:
            pass

        try:
            if use_serper:
                links = _serper_links(
                    query,
                    limit=max(2, int(max_links or 2) * 2),
                    timeout=fetch_timeout,
                    api_key=serper_api_key,
                    cache=query_cache,
                )
            else:
                links = _ddg_links(
                    query,
                    limit=max(2, int(max_links or 2) * 2),
                    timeout=fetch_timeout,
                    cache=query_cache,
                )
        except Exception:
            continue
        ranked = _rank_links(links, orgao_hint=orgao_hint)
        to_check = ranked[: max(1, int(max_links or 1))]

        def _probe(link):
            try:
                text = _strip_html(_fetch_text(link, timeout=fetch_timeout, cache=page_cache))
            except Exception:
                return None
            if _is_low_quality_source(link):
                reject_reasons.append(f"low_quality:{link}")
                return None
            if official_only and not _is_official_source(link):
                reject_reasons.append(f"non_official:{link}")
                return None
            if cidade_hint and not _mentions_city(text, cidade_hint):
                reject_reasons.append(f"city_mismatch:{link}")
                return None
            candidate = _extract_best_address_candidate(
                nome,
                cargo_orgao,
                link,
                text,
                preferred_city=cidade_hint,
            )
            if candidate:
                if _is_low_quality_source(link):
                    candidate["score"] -= 6
                return candidate
            return None

        with ThreadPoolExecutor(max_workers=max(1, int(workers or 1))) as executor:
            futures = {executor.submit(_probe, link): link for link in to_check}
            for future in as_completed(futures):
                found = future.result()
                if found:
                    if best_candidate is None or found["score"] > best_candidate["score"]:
                        best_candidate = found
                    # Curtocircuito quando confiança está alta.
                    if found["score"] >= 12:
                        break
        if best_candidate and best_candidate["score"] >= 12:
            break
    if best_candidate:
        if int(min_score or 0) > 0 and int(best_candidate["score"]) < int(min_score):
            if return_meta:
                return None, {
                    "ok": False,
                    "reason": "below_min_score",
                    "best_score": best_candidate["score"],
                    "best_link": best_candidate["link"],
                    "reject_reasons": reject_reasons[-10:],
                }
            return None
        result = (best_candidate["endereco"], best_candidate["link"])
        if orgao_cache is not None and orgao_key:
            orgao_cache[orgao_key] = result
        if return_meta:
            return result, {
                "ok": True,
                "score": best_candidate["score"],
                "link": best_candidate["link"],
                "reject_reasons": reject_reasons[-10:],
            }
        return result
    if return_meta:
        return None, {"ok": False, "reason": "no_candidate", "reject_reasons": reject_reasons[-10:]}
    return None


class Command(BaseCommand):
    help = (
        "Atualiza o texto de etiqueta (dados_etiqueta) usando dados internos "
        "(Municipe + PerfilMunicipe), com filtros opcionais por categoria e IDs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Salva alterações no banco. Sem isso, roda em simulação (dry-run).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Sobrescreve etiquetas já preenchidas. Sem isso, pula registros com dados_etiqueta.",
        )
        parser.add_argument(
            "--categoria-ids",
            nargs="*",
            default=[],
            help="Uma ou mais categorias (ex.: --categoria-ids 1 2 ou --categoria-ids 1,2). Se vazio, considera todas.",
        )
        parser.add_argument(
            "--ids",
            nargs="*",
            default=[],
            help="Um ou mais IDs de munícipe (ex.: --ids 1116 1115 ou --ids 1116,1115). Se vazio, considera todos.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limite máximo de contatos processados (0 = sem limite).",
        )
        parser.add_argument(
            "--hybrid-web",
            action="store_true",
            help="Quando endereço interno faltar, tenta buscar endereço externo para a etiqueta.",
        )
        parser.add_argument(
            "--hybrid-limit",
            type=int,
            default=30,
            help="Máximo de contatos para tentativa externa no modo híbrido.",
        )
        parser.add_argument(
            "--hybrid-max-queries",
            type=int,
            default=3,
            help="Máximo de queries web por contato no modo híbrido.",
        )
        parser.add_argument(
            "--hybrid-max-links",
            type=int,
            default=4,
            help="Máximo de links analisados por query no modo híbrido.",
        )
        parser.add_argument(
            "--hybrid-timeout",
            type=float,
            default=5.0,
            help="Timeout (segundos) por requisição web no modo híbrido.",
        )
        parser.add_argument(
            "--hybrid-workers",
            type=int,
            default=4,
            help="Quantidade de workers paralelos por query no modo híbrido.",
        )
        parser.add_argument(
            "--web-provider",
            type=str,
            default="auto",
            help="Provedor de busca web: auto|ddg|serper (serper requer SERPER_API_KEY).",
        )
        parser.add_argument(
            "--preferred-city",
            type=str,
            default="",
            help="Cidade preferencial para desempate de endereço no híbrido (ex.: 'Mogi das Cruzes').",
        )
        parser.add_argument(
            "--hybrid-official-only",
            action="store_true",
            help="Aceita apenas fontes oficiais (.gov.br/.jus.br/.mp.br/.leg.br) no híbrido.",
        )
        parser.add_argument(
            "--hybrid-min-score",
            type=int,
            default=10,
            help="Score mínimo para aceitar candidato de endereço no híbrido.",
        )
        parser.add_argument(
            "--hybrid-audit-file",
            type=str,
            default="",
            help="Caminho de arquivo JSONL para auditoria detalhada do híbrido.",
        )

    def handle(self, *args, **options):
        categoria_ids = _parse_int_list(options.get("categoria_ids"))
        ids = _parse_int_list(options.get("ids"))
        force = bool(options.get("force"))
        commit = bool(options.get("commit"))
        limit = int(options.get("limit") or 0)
        hybrid_web = bool(options.get("hybrid_web"))
        hybrid_limit = max(0, int(options.get("hybrid_limit") or 0))
        hybrid_max_queries = max(1, int(options.get("hybrid_max_queries") or 1))
        hybrid_max_links = max(1, int(options.get("hybrid_max_links") or 1))
        hybrid_timeout = max(1.0, float(options.get("hybrid_timeout") or 1.0))
        hybrid_workers = max(1, int(options.get("hybrid_workers") or 1))
        web_provider = (options.get("web_provider") or "auto").strip().lower()
        preferred_city = (options.get("preferred_city") or "").strip()
        hybrid_official_only = bool(options.get("hybrid_official_only"))
        hybrid_min_score = int(options.get("hybrid_min_score") or 0)
        hybrid_audit_file = (options.get("hybrid_audit_file") or "").strip()
        serper_api_key = (os.getenv("SERPER_API_KEY") or "").strip()
        hybrid_cache = {"query_links": {}, "page_text": {}, "orgao_address": {}}
        hybrid_audit_rows = []

        qs = Municipe.objects.all().prefetch_related("perfis__categoria")
        if categoria_ids:
            qs = qs.filter(perfis__categoria_id__in=categoria_ids)
        if ids:
            qs = qs.filter(id__in=ids)
        qs = qs.distinct().order_by("nome_completo")
        if limit > 0:
            qs = qs[:limit]

        total = qs.count()
        atualizados = 0
        ignorados_ja_preenchidos = 0
        ignorados_sem_perfil = 0
        hibridos_tentados = 0
        hibridos_sucesso = 0
        amostra = []

        self.stdout.write(self.style.WARNING("--- ATUALIZAÇÃO DE ETIQUETAS (DADOS INTERNOS) ---"))
        self.stdout.write(f"Filtro categorias: {categoria_ids or 'TODAS'}")
        self.stdout.write(f"Filtro IDs: {ids or 'TODOS'}")
        self.stdout.write(f"Modo: {'COMMIT' if commit else 'SIMULAÇÃO'} | Force: {force} | Total alvo: {total}")
        if hybrid_web:
            provider_used = "serper" if web_provider in {"serper", "auto"} and serper_api_key else "ddg"
            self.stdout.write(f"Web provider (híbrido): {provider_used}")
            if preferred_city:
                self.stdout.write(f"Cidade preferencial (híbrido): {preferred_city}")
            self.stdout.write(f"Somente fontes oficiais: {hybrid_official_only}")
            self.stdout.write(f"Score mínimo híbrido: {hybrid_min_score}")

        for m in qs:
            if m.dados_etiqueta and not force:
                ignorados_ja_preenchidos += 1
                continue

            perfis = m.perfis.filter(ativo=True)
            if categoria_ids:
                perfis = perfis.filter(categoria_id__in=categoria_ids)
            perfil_ref = perfis.order_by("-id").first()
            if not perfil_ref:
                ignorados_sem_perfil += 1
                continue
            categoria_nome = ((perfil_ref.categoria.nome if perfil_ref.categoria else "") or "").strip()

            cargo = _smart_title((perfil_ref.cargo or "").strip())
            orgao = _smart_title((perfil_ref.instituicao or "").strip())
            cargo_orgao = " - ".join([x for x in [cargo, orgao] if x]) or _smart_title(m.cargo or m.orgao or "Não informado")

            tratamento = (perfil_ref.tratamento or m.tratamento or "").strip()
            tratamento = _infer_tratamento(m.nome_completo, cargo_orgao, tratamento)

            end_l1, end_l2 = _format_endereco(m.endereco)
            fonte_endereco = "interno"

            should_try_hybrid = _should_attempt_hybrid(
                end_l1,
                end_l2,
                cargo_orgao,
                categoria_nome,
                force=force,
            )
            if hybrid_web and should_try_hybrid and hibridos_tentados < hybrid_limit:
                hibridos_tentados += 1
                achado, meta = _buscar_endereco_externo(
                    m.nome_completo,
                    cargo_orgao,
                    max_queries=hybrid_max_queries,
                    max_links=hybrid_max_links,
                    fetch_timeout=hybrid_timeout,
                    workers=hybrid_workers,
                    cache=hybrid_cache,
                    web_provider=web_provider,
                    serper_api_key=serper_api_key,
                    preferred_city=preferred_city,
                    official_only=hybrid_official_only,
                    min_score=hybrid_min_score,
                    return_meta=True,
                )
                if achado:
                    (end_l1, end_l2), _link = achado
                    fonte_endereco = f"web:{_link}"
                    hibridos_sucesso += 1
                hybrid_audit_rows.append(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "municipe_id": m.id,
                        "nome": m.nome_completo,
                        "categoria": categoria_nome,
                        "cargo_orgao": cargo_orgao,
                        "provider": web_provider,
                        "preferred_city": preferred_city,
                        "official_only": hybrid_official_only,
                        "min_score": hybrid_min_score,
                        "resultado_ok": bool(meta and meta.get("ok")),
                        "score": (meta or {}).get("score"),
                        "link": (meta or {}).get("link"),
                        "motivo": (meta or {}).get("reason"),
                        "reject_reasons": (meta or {}).get("reject_reasons", []),
                    }
                )
            novo_texto = "\n".join(
                [
                    tratamento,
                    _smart_title(m.nome_completo or "").strip(),
                    cargo_orgao.strip(),
                    end_l1,
                    end_l2,
                ]
            ).strip()

            if commit:
                m.dados_etiqueta = novo_texto
                m.save(update_fields=["dados_etiqueta"])
            atualizados += 1

            if len(amostra) < 10:
                amostra.append(f"ID {m.id} | {m.nome_completo} | fonte_endereco={fonte_endereco}")

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(f"TOTAL ALVO: {total}")
        self.stdout.write(f"ATUALIZADOS {'(simulação)' if not commit else ''}: {atualizados}")
        self.stdout.write(f"IGNORADOS (já preenchidos, sem --force): {ignorados_ja_preenchidos}")
        self.stdout.write(f"IGNORADOS (sem perfil elegível): {ignorados_sem_perfil}")
        if hybrid_web:
            self.stdout.write(f"HÍBRIDO EXTERNO (tentados/sucesso): {hibridos_tentados}/{hibridos_sucesso}")
        self.stdout.write("=" * 60)
        if amostra:
            self.stdout.write("AMOSTRA PROCESSADA:")
            for item in amostra:
                self.stdout.write(f"- {item}")

        if hybrid_audit_file and hybrid_web and hybrid_audit_rows:
            try:
                parent = os.path.dirname(hybrid_audit_file)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(hybrid_audit_file, "a", encoding="utf-8") as f:
                    for row in hybrid_audit_rows:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                self.stdout.write(f"Auditoria híbrida salva em: {hybrid_audit_file}")
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Falha ao salvar auditoria híbrida: {exc}"))

        if not commit:
            self.stdout.write(self.style.SUCCESS("SIMULAÇÃO FINALIZADA. Use --commit para aplicar."))
        else:
            self.stdout.write(self.style.SUCCESS("ATUALIZAÇÃO CONCLUÍDA COM SUCESSO."))