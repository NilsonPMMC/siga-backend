"""
Mescla histórica de CategoriaAtendimento (M2M) para AssuntoAtendimento (FK única).
"""
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from django.db import transaction

from ..models import AssuntoAtendimento, Atendimento, CategoriaAtendimento

# Assuntos adicionais para categorias que não cabem nos 11 da seed inicial
ASSUNTOS_EXTRAS = [
    ('CULTURA', 'cultura', 110),
    ('ESPORTE E LAZER', 'esporte_lazer', 115),
    ('ELOGIO', 'elogio', 120),
    ('RECLAMAÇÃO', 'reclamacao', 125),
    ('OUVIDORIA', 'ouvidoria', 130),
    ('PROCON', 'procon', 131),
    ('CONTROLADORIA', 'controladoria', 132),
    ('IPREM', 'iprem', 133),
    ('PROCURADORIA', 'procuradoria', 134),
    ('LONGEVIDADE', 'longevididade', 135),
    ('MULHER', 'mulher', 136),
    ('SERVIÇO MILITAR', 'servico_militar', 137),
]

# Nome da categoria (normalizado) → codigo do assunto
MAPEAMENTO_CATEGORIA_PARA_CODIGO = {
    'SAUDE': 'saude',
    'EDUCACAO': 'educacao',
    'HABITACAO': 'habitacao',
    'ASSISTENCIA SOCIAL': 'assistencia_social',
    'SEGURANCA PUBLICA': 'seguranca',
    'MEIO AMBIENTE E PROTECAO ANIMAL': 'meio_ambiente',
    'MOBILIDADE E TRANSITO': 'transporte',
    'ESPORTE E LAZER': 'esporte_lazer',
    'CULTURA': 'cultura',
    'SEMAE': 'meio_ambiente',
    'SERVICOS URBANOS E ZELADORIA': 'obras',
    'FUNDO SOCIAL': 'assistencia_social',
    'ELOGIO': 'elogio',
    'RECLAMACAO': 'reclamacao',
    'CONTROLADORIA': 'controladoria',
    'IPREM': 'iprem',
    'OUVIDORIA': 'ouvidoria',
    'PROCON': 'procon',
    'PROCURADORIA': 'procuradoria',
    'LONGEVIDADE': 'longevididade',
    'MULHER': 'mulher',
    'SERVICO MILITAR': 'servico_militar',
}


def normalizar_nome(valor: str) -> str:
    if not valor:
        return ''
    texto = unicodedata.normalize('NFKD', valor)
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r'[^A-Za-z0-9\s]', ' ', texto)
    return ' '.join(texto.upper().split())


def garantir_assuntos_extras() -> Dict[str, AssuntoAtendimento]:
    """Cria assuntos extras e retorna mapa codigo → instância."""
    por_codigo = {}
    for nome, codigo, ordem in ASSUNTOS_EXTRAS:
        obj, _ = AssuntoAtendimento.objects.get_or_create(
            codigo=codigo,
            defaults={'nome': nome, 'ordem': ordem, 'ativo': True},
        )
        por_codigo[codigo] = obj
    for obj in AssuntoAtendimento.objects.filter(ativo=True):
        por_codigo[obj.codigo] = obj
    return por_codigo


def resolver_assunto_por_categoria_nome(
    nome_categoria: str,
    assuntos_por_codigo: Optional[Dict[str, AssuntoAtendimento]] = None,
) -> Optional[AssuntoAtendimento]:
    if assuntos_por_codigo is None:
        assuntos_por_codigo = garantir_assuntos_extras()
    chave = normalizar_nome(nome_categoria)
    codigo = MAPEAMENTO_CATEGORIA_PARA_CODIGO.get(chave)
    if not codigo:
        return assuntos_por_codigo.get('outros')
    return assuntos_por_codigo.get(codigo) or assuntos_por_codigo.get('outros')


def executar_mescla(
    *,
    aplicar: bool = False,
    limpar_m2m: bool = False,
    desativar_categorias: bool = False,
    preencher_sem_assunto_outros: bool = False,
    forcar_de_categoria: bool = False,
    limite: Optional[int] = None,
) -> dict:
    """
    Migra vínculos M2M de categorias para FK assunto.
    Retorna estatísticas e listas de conflitos/pendências.
    """
    assuntos_map = garantir_assuntos_extras()
    outros = assuntos_map.get('outros')

    stats = {
        'atendimentos_com_categorias': 0,
        'assunto_preenchido_de_categoria': 0,
        'conflitos': 0,
        'm2m_removidos': 0,
        'sem_assunto_preenchidos_outros': 0,
        'categorias_desativadas': 0,
    }
    conflitos: List[str] = []
    amostra_preenchidos: List[str] = []

    qs = Atendimento.objects.prefetch_related('categorias', 'assunto').order_by('id')
    if limite:
        qs = qs[:limite]

    with transaction.atomic():
        for atendimento in qs:
            cats = list(atendimento.categorias.all())
            if cats:
                stats['atendimentos_com_categorias'] += 1
                alvo = resolver_assunto_por_categoria_nome(cats[0].nome, assuntos_map)
                if not alvo:
                    alvo = outros

                if atendimento.assunto_id and not forcar_de_categoria:
                    if alvo and atendimento.assunto_id != alvo.id:
                        stats['conflitos'] += 1
                        conflitos.append(
                            f"id={atendimento.id} protocolo={atendimento.protocolo} "
                            f"assunto={atendimento.assunto.nome} categoria={cats[0].nome}"
                        )
                elif alvo:
                    if aplicar:
                        atendimento.assunto = alvo
                        atendimento.save(update_fields=['assunto'])
                    stats['assunto_preenchido_de_categoria'] += 1
                    if len(amostra_preenchidos) < 15:
                        amostra_preenchidos.append(
                            f"{atendimento.protocolo or atendimento.id} ← {cats[0].nome} → {alvo.nome}"
                        )

                if limpar_m2m and aplicar and cats:
                    atendimento.categorias.clear()
                    stats['m2m_removidos'] += 1

            elif preencher_sem_assunto_outros and not atendimento.assunto_id and outros:
                if aplicar:
                    atendimento.assunto = outros
                    atendimento.save(update_fields=['assunto'])
                stats['sem_assunto_preenchidos_outros'] += 1

        if desativar_categorias and aplicar:
            stats['categorias_desativadas'] = CategoriaAtendimento.objects.filter(
                ativa=True
            ).update(ativa=False)

    return {
        'stats': stats,
        'conflitos': conflitos,
        'amostra_preenchidos': amostra_preenchidos,
        'mapeamento': MAPEAMENTO_CATEGORIA_PARA_CODIGO,
        'assuntos_extras': ASSUNTOS_EXTRAS,
    }
