"""SLA gerencial de atendimentos (Épico 10)."""
from datetime import timedelta

from django.db.models import F, Q
from django.utils import timezone

from ..models import Atendimento, SlaAtendimentoConfig

SLA_STATUS_CHOICES = (
    ('NO_PRAZO', 'No prazo'),
    ('EM_RISCO', 'Em risco'),
    ('VENCIDO', 'Vencido'),
)

DEFAULT_DIAS_RESPOSTA = 3
DEFAULT_DIAS_CONCLUSAO = 10
RISCO_DIAS_CALENDARIO = 2

STATUS_ABERTOS = ('ABERTO', 'EM_ANALISE', 'ENCAMINHADO')
STATUS_FINALIZADOS = ('CONCLUIDO', 'ARQUIVADO')


def add_business_days(inicio, dias):
    """Soma dias úteis (seg–sex) a partir de um datetime aware."""
    if dias <= 0:
        return inicio
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual += timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def resolver_config_sla(conta_id, assunto_id=None):
    config = None
    if assunto_id:
        config = SlaAtendimentoConfig.objects.filter(
            conta_id=conta_id, assunto_id=assunto_id, ativo=True
        ).first()
    if not config:
        config = SlaAtendimentoConfig.objects.filter(
            conta_id=conta_id, assunto__isnull=True, ativo=True
        ).first()
    if config:
        return {
            'dias_resposta': config.dias_resposta,
            'dias_conclusao': config.dias_conclusao,
        }
    return {
        'dias_resposta': DEFAULT_DIAS_RESPOSTA,
        'dias_conclusao': DEFAULT_DIAS_CONCLUSAO,
    }


def calcular_prazos_sla(atendimento):
    cfg = resolver_config_sla(atendimento.conta_id, atendimento.assunto_id)
    base = atendimento.data_criacao or timezone.now()
    return {
        'prazo_resposta': add_business_days(base, cfg['dias_resposta']),
        'prazo_conclusao': add_business_days(base, cfg['dias_conclusao']),
    }


def garantir_prazos_sla(atendimento, force=False):
    """Define prazos no atendimento conforme conta/assunto."""
    if not force and atendimento.prazo_resposta and atendimento.prazo_conclusao:
        return atendimento
    prazos = calcular_prazos_sla(atendimento)
    Atendimento.objects.filter(pk=atendimento.pk).update(**prazos)
    atendimento.prazo_resposta = prazos['prazo_resposta']
    atendimento.prazo_conclusao = prazos['prazo_conclusao']
    return atendimento


def recalcular_prazos_abertos(conta_id=None, assunto_id=None):
    """
    Recalcula prazos de atendimentos abertos quando a config SLA muda
    ou após backfill com defaults desatualizados.
    """
    qs = Atendimento.objects.filter(status__in=STATUS_ABERTOS)
    if conta_id is not None:
        qs = qs.filter(conta_id=conta_id)
    if assunto_id is not None:
        qs = qs.filter(assunto_id=assunto_id)
    atualizados = 0
    for atendimento in qs.iterator(chunk_size=500):
        garantir_prazos_sla(atendimento, force=True)
        atualizados += 1
    return atualizados


def calcular_sla_status(atendimento, agora=None):
    agora = agora or timezone.now()
    prazo = atendimento.prazo_conclusao
    if not prazo:
        return None

    if atendimento.status in STATUS_FINALIZADOS:
        referencia = atendimento.data_atualizacao or agora
        return 'NO_PRAZO' if referencia <= prazo else 'VENCIDO'

    if atendimento.status not in STATUS_ABERTOS:
        return None

    if agora > prazo:
        return 'VENCIDO'

    limite_risco = agora + timedelta(days=RISCO_DIAS_CALENDARIO)
    if prazo <= limite_risco:
        return 'EM_RISCO'

    inicio = atendimento.data_criacao or agora
    total = (prazo - inicio).total_seconds()
    if total > 0:
        restante = (prazo - agora).total_seconds()
        if restante / total <= 0.2:
            return 'EM_RISCO'

    return 'NO_PRAZO'


def sla_status_display(codigo):
    return dict(SLA_STATUS_CHOICES).get(codigo, codigo or '—')


def filtrar_queryset_por_sla(queryset, sla_status, agora=None):
    agora = agora or timezone.now()
    sla_status = (sla_status or '').upper()
    if sla_status not in dict(SLA_STATUS_CHOICES):
        return queryset

    com_prazo = Q(prazo_conclusao__isnull=False)
    abertos = Q(status__in=STATUS_ABERTOS)
    limite_risco = agora + timedelta(days=RISCO_DIAS_CALENDARIO)

    if sla_status == 'VENCIDO':
        return queryset.filter(com_prazo).filter(
            Q(status__in=STATUS_ABERTOS, prazo_conclusao__lt=agora)
            | Q(
                status__in=STATUS_FINALIZADOS,
                data_atualizacao__gt=F('prazo_conclusao'),
            )
        )

    if sla_status == 'EM_RISCO':
        return queryset.filter(
            abertos,
            com_prazo,
            prazo_conclusao__gte=agora,
            prazo_conclusao__lte=limite_risco,
        )

    if sla_status == 'NO_PRAZO':
        return queryset.filter(
            abertos,
            com_prazo,
            prazo_conclusao__gt=limite_risco,
        )

    return queryset


def resumo_sla_queryset(queryset, agora=None):
    agora = agora or timezone.now()
    totais = {'NO_PRAZO': 0, 'EM_RISCO': 0, 'VENCIDO': 0, 'SEM_SLA': 0}
    for atendimento in queryset.iterator(chunk_size=500):
        status = calcular_sla_status(atendimento, agora)
        if status:
            totais[status] += 1
        else:
            totais['SEM_SLA'] += 1
    com_sla = sum(totais[k] for k in ('NO_PRAZO', 'EM_RISCO', 'VENCIDO'))
    pct_no_prazo = round(100 * totais['NO_PRAZO'] / com_sla, 1) if com_sla else 0.0
    return {
        'totais': totais,
        'com_sla': com_sla,
        'pct_no_prazo': pct_no_prazo,
    }


def resumo_sla_por_dimensao(queryset, campo, agora=None):
    agora = agora or timezone.now()
    agrupado = {}
    for atendimento in queryset.select_related(campo).iterator(chunk_size=500):
        rel = getattr(atendimento, campo, None)
        chave = getattr(rel, 'nome', None) or str(getattr(rel, 'pk', '—'))
        item = agrupado.setdefault(
            chave,
            {'nome': chave, 'total': 0, 'NO_PRAZO': 0, 'EM_RISCO': 0, 'VENCIDO': 0},
        )
        item['total'] += 1
        status = calcular_sla_status(atendimento, agora)
        if status:
            item[status] += 1
    rows = []
    for row in agrupado.values():
        com_sla = row['NO_PRAZO'] + row['EM_RISCO'] + row['VENCIDO']
        row['pct_no_prazo'] = round(100 * row['NO_PRAZO'] / com_sla, 1) if com_sla else 0.0
        rows.append(row)
    rows.sort(key=lambda r: (-r['total'], r['nome']))
    return rows
