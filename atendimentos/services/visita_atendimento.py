"""
Unificação RegistroVisita → Atendimento (Fase 4).
"""
import logging
from typing import Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import AssuntoAtendimento, Atendimento, RegistroVisita

logger = logging.getLogger(__name__)

CODIGO_ASSUNTO_VISITA = 'visita_recepcao'


def obter_assunto_visita():
    assunto = AssuntoAtendimento.objects.filter(codigo=CODIGO_ASSUNTO_VISITA, ativo=True).first()
    if not assunto:
        assunto = AssuntoAtendimento.objects.filter(ativo=True).order_by('-ordem').first()
    return assunto


def _status_padrao_visita():
    return getattr(settings, 'ATENDIMENTO_VISITA_STATUS_PADRAO', 'CONCLUIDO')


def _prefixo_titulo_visita():
    return getattr(settings, 'ATENDIMENTO_VISITA_TITULO_PREFIX', 'VISITA')


def _gerar_protocolo_para_ano(ano: int) -> str:
    ultimo = (
        Atendimento.objects.filter(protocolo__startswith=f'{ano}-')
        .order_by('protocolo')
        .last()
    )
    numero = int(ultimo.protocolo.split('-')[1]) + 1 if ultimo else 1
    return f'{ano}-{numero:05d}'


def _montar_titulo_visita(nome_municipe: str) -> str:
    prefixo = _prefixo_titulo_visita()
    nome = (nome_municipe or 'MUNÍCIPE').strip().upper()
    return f'{prefixo} — {nome}'[:255]


def _montar_descricao_visita(observacao: Optional[str]) -> str:
    texto = (observacao or '').strip()
    if texto:
        return texto.upper()
    return 'REGISTRO DE VISITA / CHECK-IN NA RECEPÇÃO.'


def criar_atendimento_a_partir_de_visita(
    *,
    municipe,
    conta_destino,
    usuario_destino=None,
    observacao=None,
    registrado_por=None,
    data_referencia=None,
    salvar=True,
) -> Atendimento:
    """
    Cria um Atendimento equivalente a um registro de visita.
    Se data_referencia for informada, preserva em data_criacao após o save.
    """
    assunto = obter_assunto_visita()
    if not assunto:
        raise ValueError('Nenhum assunto ativo encontrado (esperado codigo visita_recepcao).')

    data_ref = data_referencia or timezone.now()
    ano = data_ref.year if hasattr(data_ref, 'year') else timezone.now().year

    atendimento = Atendimento(
        titulo=_montar_titulo_visita(municipe.nome_completo),
        descricao=_montar_descricao_visita(observacao),
        origem='PRESENCIAL',
        status=_status_padrao_visita(),
        assunto=assunto,
        conta=conta_destino,
        municipe=municipe,
        responsavel=usuario_destino,
        created_by=registrado_por,
        protocolo=_gerar_protocolo_para_ano(ano),
        assunto_ia_status='REVISADO',
    )
    if salvar:
        atendimento.save()
        if data_referencia:
            Atendimento.objects.filter(pk=atendimento.pk).update(
                data_criacao=data_referencia,
                data_atualizacao=data_referencia,
            )
            atendimento.refresh_from_db()
    return atendimento


@transaction.atomic
def migrar_registro_visita(
    visita: RegistroVisita,
    *,
    dry_run: bool = True,
    sobrescrever: bool = False,
) -> Tuple[str, Optional[Atendimento]]:
    """
    Migra um RegistroVisita para Atendimento e vincula FK atendimento.
    Retorna (status, atendimento).
    """
    if visita.atendimento_id and not sobrescrever:
        return 'ja_vinculado', visita.atendimento

    if dry_run:
        return 'simulado', None

    if visita.atendimento_id and sobrescrever:
        visita.atendimento.delete()
        visita.atendimento_id = None

    atendimento = criar_atendimento_a_partir_de_visita(
        municipe=visita.municipe,
        conta_destino=visita.conta_destino,
        usuario_destino=visita.usuario_destino,
        observacao=visita.observacao,
        registrado_por=visita.registrado_por,
        data_referencia=visita.data_checkin,
    )
    visita.atendimento = atendimento
    visita.save(update_fields=['atendimento'])
    return 'migrado', atendimento


@transaction.atomic
def registrar_visita_como_atendimento(
    *,
    municipe,
    conta_destino,
    usuario_destino=None,
    observacao=None,
    registrado_por=None,
    manter_registro_legado: bool = True,
) -> Tuple[Atendimento, Optional[RegistroVisita]]:
    """
    Fluxo unificado para novos check-ins: Atendimento é o registro principal.
    Opcionalmente mantém RegistroVisita vinculado (compatibilidade de listagens/BI).
    """
    atendimento = criar_atendimento_a_partir_de_visita(
        municipe=municipe,
        conta_destino=conta_destino,
        usuario_destino=usuario_destino,
        observacao=observacao,
        registrado_por=registrado_por,
    )

    registro = None
    if manter_registro_legado:
        registro = RegistroVisita.objects.create(
            municipe=municipe,
            conta_destino=conta_destino,
            usuario_destino=usuario_destino,
            observacao=observacao,
            registrado_por=registrado_por,
            atendimento=atendimento,
        )
        RegistroVisita.objects.filter(pk=registro.pk).update(
            data_checkin=atendimento.data_criacao
        )
        registro.refresh_from_db()

    return atendimento, registro


def queryset_atendimentos_visita():
    """Atendimentos classificados como visita/recepção."""
    return (
        Atendimento.objects.filter(assunto__codigo=CODIGO_ASSUNTO_VISITA)
        .select_related('municipe', 'conta', 'responsavel', 'assunto', 'created_by')
    )


def serializar_atendimento_como_visita_agenda(atendimento) -> dict:
    """Formato compatível com o frontend legado de agenda/recepção (DataTable do dashboard)."""
    responsavel = atendimento.responsavel
    return {
        'id': atendimento.id,
        'atendimento': atendimento.id,
        'municipe': atendimento.municipe_id,
        'municipe_nome': atendimento.municipe.nome_completo if atendimento.municipe else '',
        'conta_destino': atendimento.conta_id,
        'conta_destino_nome': atendimento.conta.nome if atendimento.conta else '',
        'usuario_destino': responsavel.id if responsavel else None,
        'usuario_destino_nome': (
            (responsavel.get_full_name() or responsavel.username) if responsavel else None
        ),
        'data_checkin': atendimento.data_criacao.isoformat() if atendimento.data_criacao else None,
        'observacao': (atendimento.descricao or '')[:500],
        'protocolo': atendimento.protocolo,
    }


def listar_visitas_agenda_serializado(user, data_obj):
    """Lista atendimentos visita/recepção do dia no formato legado da agenda."""
    from datetime import datetime, time as dt_time

    from django.db.models import Q

    from ..models import Conta

    contas_usuario = user.perfil.contas.all() if hasattr(user, 'perfil') else Conta.objects.none()
    if user.is_superuser and not contas_usuario.exists():
        contas_usuario = Conta.objects.all()
    if not contas_usuario.exists():
        return []

    inicio = timezone.make_aware(datetime.combine(data_obj, dt_time.min))
    fim = timezone.make_aware(datetime.combine(data_obj, dt_time.max))
    qs = (
        queryset_atendimentos_visita()
        .filter(conta__in=contas_usuario, data_criacao__range=(inicio, fim))
        .filter(
            Q(responsavel=user) | Q(created_by=user) | Q(responsavel__isnull=True)
        )
        .select_related('municipe', 'conta', 'responsavel')
        .order_by('data_criacao')
    )
    return [serializar_atendimento_como_visita_agenda(a) for a in qs]
