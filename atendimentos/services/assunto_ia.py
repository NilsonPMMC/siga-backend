"""
Classificação de assunto de atendimento via LLM (lista fechada de AssuntoAtendimento).
"""
import json
import logging
import threading
from typing import Any, Dict, List, Optional

from django.conf import settings

from ..models import AssuntoAtendimento, Atendimento, PerfilMunicipe
from .ia_intelligence import _chamar_llm_generate, _limpar_json_markdown

logger = logging.getLogger(__name__)

CODIGO_FALLBACK = 'outros'


def _lista_assuntos_ativos() -> List[dict]:
    return list(
        AssuntoAtendimento.objects.filter(ativo=True)
        .order_by('ordem', 'nome')
        .values('id', 'codigo', 'nome')
    )


def _contexto_municipe(municipe_id: Optional[int], conta_id: Optional[int]) -> str:
    if not municipe_id or not conta_id:
        return ''
    perfil = (
        PerfilMunicipe.objects.filter(
            municipe_id=municipe_id,
            conta_id=conta_id,
            ativo=True,
        )
        .order_by('-id')
        .first()
    )
    partes = []
    if perfil:
        if perfil.cargo:
            partes.append(f"Cargo: {perfil.cargo}")
        orgao = perfil.instituicao or perfil.departamento
        if orgao:
            partes.append(f'Órgão: {orgao}')
    else:
        from ..models import Municipe
        try:
            m = Municipe.objects.get(pk=municipe_id)
            if m.cargo:
                partes.append(f"Cargo: {m.cargo}")
            if m.orgao:
                partes.append(f'Órgão: {m.orgao}')
        except Municipe.DoesNotExist:
            pass
    return '\n'.join(partes)


def _resolver_assunto_por_codigo(codigo: str, assuntos: List[dict]) -> Optional[AssuntoAtendimento]:
    codigo = (codigo or '').strip().lower()
    for item in assuntos:
        if item['codigo'] == codigo:
            return AssuntoAtendimento.objects.filter(pk=item['id'], ativo=True).first()
    return (
        AssuntoAtendimento.objects.filter(codigo=CODIGO_FALLBACK, ativo=True).first()
        or AssuntoAtendimento.objects.filter(ativo=True).order_by('-ordem').first()
    )


def _classificar_com_llm(
    titulo: str,
    descricao: str,
    origem: str,
    ctx_municipe: str,
    assuntos: List[dict],
) -> Optional[Dict[str, Any]]:
    lista_txt = '\n'.join(
        f"- codigo: {a['codigo']} | nome: {a['nome']}" for a in assuntos
    )
    system = (
        'Você classifica atendimentos de gabinete público brasileiro. '
        'Escolha APENAS um código da lista fornecida — nunca invente códigos novos. '
        'Se não houver correspondência clara, use o código "outros". '
        'Responda ESTRITAMENTE com JSON válido: '
        '{"codigo": "<codigo>", "confianca": <número 0 a 1>, "justificativa": "<frase curta>"}'
    )
    prompt = f"""Classifique o atendimento abaixo.

LISTA FECHADA DE ASSUNTOS (use exatamente um codigo):
{lista_txt}

DADOS DO ATENDIMENTO:
- Origem: {origem or 'Não informada'}
- Título: {titulo or '(vazio)'}
- Descrição: {descricao or '(vazio)'}
{f'- Perfil do munícipe: {ctx_municipe}' if ctx_municipe else ''}

JSON:"""

    raw = _chamar_llm_generate(prompt, system=system)
    if not raw:
        return None
    try:
        obj = json.loads(_limpar_json_markdown(raw))
    except (json.JSONDecodeError, TypeError):
        logger.warning('assunto_ia: JSON inválido: %s', raw[:300])
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def sugerir_assunto_atendimento(
    atendimento: Atendimento,
    aplicar: bool = False,
) -> Dict[str, Any]:
    """
    Sugere assunto para um atendimento persistido.
    Não levanta exceção — retorna dict com ok=False em falhas.
    """
    assuntos = _lista_assuntos_ativos()
    if not assuntos:
        return {'ok': False, 'erro': 'Nenhum assunto ativo cadastrado.'}

    origem = (
        atendimento.get_origem_display()
        if hasattr(atendimento, 'get_origem_display')
        else (atendimento.origem or '')
    )
    parsed = _classificar_com_llm(
        titulo=(atendimento.titulo or '').strip(),
        descricao=(atendimento.descricao or '').strip(),
        origem=origem,
        ctx_municipe=_contexto_municipe(atendimento.municipe_id, atendimento.conta_id),
        assuntos=assuntos,
    )
    if not parsed:
        atendimento.assunto_ia_status = 'ERRO'
        atendimento.save(update_fields=['assunto_ia_status'])
        return {'ok': False, 'erro': 'IA indisponível ou resposta inválida.'}

    codigo = str(parsed.get('codigo', '')).strip().lower()
    try:
        confianca = float(parsed.get('confianca', 0) or 0)
    except (TypeError, ValueError):
        confianca = 0.0
    confianca = max(0.0, min(1.0, confianca))
    justificativa = str(parsed.get('justificativa', '')).strip()[:500]

    assunto_obj = _resolver_assunto_por_codigo(codigo, assuntos)
    if not assunto_obj:
        atendimento.assunto_ia_status = 'ERRO'
        atendimento.save(update_fields=['assunto_ia_status'])
        return {'ok': False, 'erro': 'Não foi possível mapear o assunto sugerido.'}

    limiar = getattr(settings, 'ATENDIMENTO_ASSUNTO_IA_CONFIANCA_MINIMA', 0.85)
    auto = getattr(settings, 'ATENDIMENTO_ASSUNTO_IA_AUTO_APLICAR', False)
    deve_aplicar = aplicar or (auto and confianca >= limiar)

    update_fields = ['assunto_ia_sugerido', 'assunto_ia_status']
    atendimento.assunto_ia_sugerido = assunto_obj
    atendimento.assunto_ia_status = 'PENDENTE'

    if deve_aplicar:
        atendimento.assunto = assunto_obj
        atendimento.assunto_ia_status = 'APLICADO'
        update_fields.append('assunto')

    atendimento.save(update_fields=update_fields)

    return {
        'ok': True,
        'assunto_id': assunto_obj.id,
        'assunto_codigo': assunto_obj.codigo,
        'assunto_nome': assunto_obj.nome,
        'confianca': confianca,
        'justificativa': justificativa,
        'aplicado': deve_aplicar,
        'assunto_ia_status': atendimento.assunto_ia_status,
    }


def sugerir_assunto_preview(
    titulo: str,
    descricao: str,
    origem: str = 'PRESENCIAL',
    municipe_id: Optional[int] = None,
    conta_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Sugestão sem persistir (formulário antes de salvar)."""
    assuntos = _lista_assuntos_ativos()
    if not assuntos:
        return {'ok': False, 'erro': 'Nenhum assunto ativo cadastrado.'}

    origem_labels = dict(Atendimento.ORIGEM_CHOICES)
    origem_txt = origem_labels.get(origem, origem)

    parsed = _classificar_com_llm(
        titulo=(titulo or '').strip(),
        descricao=(descricao or '').strip(),
        origem=origem_txt,
        ctx_municipe=_contexto_municipe(municipe_id, conta_id),
        assuntos=assuntos,
    )
    if not parsed:
        return {'ok': False, 'erro': 'IA indisponível ou resposta inválida.'}

    codigo = str(parsed.get('codigo', '')).strip().lower()
    try:
        confianca = float(parsed.get('confianca', 0) or 0)
    except (TypeError, ValueError):
        confianca = 0.0
    confianca = max(0.0, min(1.0, confianca))
    justificativa = str(parsed.get('justificativa', '')).strip()[:500]

    assunto_obj = _resolver_assunto_por_codigo(codigo, assuntos)
    if not assunto_obj:
        return {'ok': False, 'erro': 'Não foi possível mapear o assunto sugerido.'}

    return {
        'ok': True,
        'assunto_id': assunto_obj.id,
        'assunto_codigo': assunto_obj.codigo,
        'assunto_nome': assunto_obj.nome,
        'confianca': confianca,
        'justificativa': justificativa,
        'aplicado': False,
    }


def disparar_assunto_ia_pos_criacao(atendimento_id: int) -> None:
    """Executa classificação em thread daemon (não bloqueia a API)."""

    def _run():
        try:
            atendimento = Atendimento.objects.select_related('municipe', 'conta').get(pk=atendimento_id)
            sugerir_assunto_atendimento(atendimento, aplicar=False)
        except Exception:
            logger.exception('assunto_ia pos-criacao falhou (id=%s)', atendimento_id)

    threading.Thread(target=_run, daemon=True).start()
