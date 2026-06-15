"""
Filtros e agregações compartilhados para relatórios e PDF de atendimentos.
"""
import os
from datetime import datetime, time

from django.conf import settings
from django.db.models import Count, F, Q
from django.utils import timezone

from .models import Atendimento, CategoriaContato, PerfilMunicipe
from .permissions import is_in_group
from .services.perfil_municipe import (
    linhas_cargo_orgao_de_perfis,
    normalizar_cargo,
    parse_categoria_ids_from_request,
)


def parse_date_query_param(valor):
    """Converte YYYY-MM-DD (query string) em date ou None."""
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor).strip()[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _query_param(request, nome):
    if hasattr(request, 'query_params'):
        return request.query_params.get(nome)
    return request.GET.get(nome)


def _query_params_list(request, nome):
    """Lê parâmetros repetidos ou separados por vírgula."""
    valores = []
    if hasattr(request, 'query_params'):
        qp = request.query_params
        if hasattr(qp, 'getlist'):
            valores = list(qp.getlist(nome))
            if not valores:
                unico = qp.get(nome)
                if unico:
                    valores = [unico]
        else:
            unico = qp.get(nome) if isinstance(qp, dict) else None
            if isinstance(unico, list):
                valores = unico
            elif unico:
                valores = [unico]
    else:
        valores = request.GET.getlist(nome)
        if not valores:
            unico = request.GET.get(nome)
            if unico:
                valores = [unico]

    resultado = []
    for valor in valores:
        if valor is None:
            continue
        for parte in str(valor).split(','):
            parte = parte.strip()
            if parte:
                resultado.append(parte)
    return resultado


def _ids_inteiros_query(request, nome):
    ids = []
    for valor in _query_params_list(request, nome):
        try:
            ids.append(int(valor))
        except (TypeError, ValueError):
            continue
    return ids


def aplicar_filtro_perfil_municipe(queryset, request):
    """
    Filtra atendimentos cujo munícipe possui PerfilMunicipe na mesma conta do atendimento
    que atenda categoria e/ou cargo informados (regra AND quando ambos presentes).
    """
    categoria_ids = _ids_inteiros_query(request, 'categoria_contato_id')
    if not categoria_ids:
        categoria_ids = _ids_inteiros_query(request, 'categoria_id')

    cargos = [normalizar_cargo(c) for c in _query_params_list(request, 'cargo') if c and str(c).strip()]
    cargos = [c for c in cargos if c]

    if not categoria_ids and not cargos:
        return queryset

    filtro = Q(
        municipe__perfis__conta_id=F('conta_id'),
        municipe__perfis__ativo=True,
    )
    if categoria_ids:
        filtro &= Q(municipe__perfis__categoria_id__in=categoria_ids)
    if cargos:
        cargo_q = Q()
        for cargo in cargos:
            cargo_q |= Q(municipe__perfis__cargo__iexact=cargo)
        filtro &= cargo_q

    return queryset.filter(filtro).distinct()


def opcoes_cargos_relatorio(user):
    qs = PerfilMunicipe.objects.filter(ativo=True).exclude(Q(cargo__isnull=True) | Q(cargo=''))
    if not user.is_superuser:
        if hasattr(user, 'perfil'):
            qs = qs.filter(conta__in=user.perfil.contas.all())
        else:
            return []
    cargos = {normalizar_cargo(c) for c in qs.values_list('cargo', flat=True) if normalizar_cargo(c)}
    return sorted(cargos)


def opcoes_categorias_relatorio(user):
    qs = CategoriaContato.objects.filter(ativa=True).order_by('nome')
    if not user.is_superuser:
        if hasattr(user, 'perfil'):
            usadas = PerfilMunicipe.objects.filter(
                ativo=True,
                conta__in=user.perfil.contas.all(),
            ).values_list('categoria_id', flat=True).distinct()
            qs = qs.filter(id__in=usadas)
        else:
            return []
    return list(qs.values('id', 'nome'))


def resumo_filtros_relatorio(request):
    """Rótulos legíveis dos filtros de perfil para PDF/CSV."""
    categoria_ids = _ids_inteiros_query(request, 'categoria_contato_id')
    if not categoria_ids:
        categoria_ids = _ids_inteiros_query(request, 'categoria_id')

    cargos = [normalizar_cargo(c) for c in _query_params_list(request, 'cargo') if normalizar_cargo(c)]

    categorias_nomes = []
    if categoria_ids:
        categorias_nomes = list(
            CategoriaContato.objects.filter(id__in=categoria_ids).order_by('nome').values_list('nome', flat=True)
        )

    return {
        'categorias': categorias_nomes,
        'cargos': cargos,
        'tem_filtro_perfil': bool(categorias_nomes or cargos),
    }


def aplicar_filtro_periodo_em_queryset(queryset, request, campo='data_criacao'):
    """Filtra queryset por data_inicio/data_fim com datetimes timezone-aware."""
    data_inicio = parse_date_query_param(_query_param(request, 'data_inicio'))
    data_fim = parse_date_query_param(_query_param(request, 'data_fim'))
    if data_inicio:
        inicio_dt = timezone.make_aware(datetime.combine(data_inicio, time.min))
        queryset = queryset.filter(**{f'{campo}__gte': inicio_dt})
    if data_fim:
        fim_dt = timezone.make_aware(datetime.combine(data_fim, time.max))
        queryset = queryset.filter(**{f'{campo}__lte': fim_dt})
    return queryset


def queryset_bi_atendimentos(user, request):
    """Mesmos filtros do painel BI e do PDF BI (tenant, período, conta, responsável)."""
    queryset = Atendimento.objects.all()

    if not user.is_superuser:
        if hasattr(user, 'perfil'):
            queryset = queryset.filter(conta__in=user.perfil.contas.all())
        else:
            return Atendimento.objects.none()

    queryset = aplicar_filtro_periodo_em_queryset(queryset, request)

    conta_id = _query_param(request, 'conta_id')
    if conta_id:
        queryset = queryset.filter(conta_id=conta_id)

    usuario_id = _query_param(request, 'usuario_id')
    if usuario_id:
        queryset = queryset.filter(responsavel_id=usuario_id)
    elif str(_query_param(request, 'apenas_meus') or '').lower() in ('true', '1'):
        queryset = queryset.filter(responsavel=user)

    queryset = aplicar_filtro_perfil_municipe(queryset, request)
    return queryset


def resolver_logos_relatorio_pdf(conta_contexto=None, request=None):
    """
    Caminhos file:// para WeasyPrint (brasão prefeitura, logo conta, logo SIGA).
    """
    nome_instituicao = 'Prefeitura Municipal'
    brasao_path = None
    logo_conta_path = None
    logo_siga_path = None

    candidatos_brasao = [
        settings.STATIC_ROOT / 'images' / 'logo-brasao-prefeitura.png',
        settings.BASE_DIR / 'static' / 'images' / 'logo-brasao-prefeitura.png',
        settings.BASE_DIR / 'staticfiles' / 'images' / 'logo-brasao-prefeitura.png',
    ]
    for caminho in candidatos_brasao:
        if caminho.exists():
            brasao_path = os.path.abspath(str(caminho)).replace('\\', '/')
            break

    if conta_contexto:
        nome_instituicao = conta_contexto.nome_instituicao or nome_instituicao
        try:
            if conta_contexto.brasao_instituicao and os.path.exists(conta_contexto.brasao_instituicao.path):
                brasao_path = os.path.abspath(conta_contexto.brasao_instituicao.path).replace('\\', '/')
        except Exception:
            pass
        try:
            if conta_contexto.logo_conta and os.path.exists(conta_contexto.logo_conta.path):
                logo_conta_path = os.path.abspath(conta_contexto.logo_conta.path).replace('\\', '/')
        except Exception:
            pass

    logo_siga_static = settings.STATIC_ROOT / 'images' / 'logo-siga-gab.png'
    if logo_siga_static.exists():
        logo_siga_path = os.path.abspath(str(logo_siga_static)).replace('\\', '/')
    else:
        logo_siga_alt = settings.BASE_DIR / 'staticfiles' / 'images' / 'logo-siga-gab.png'
        if logo_siga_alt.exists():
            logo_siga_path = os.path.abspath(str(logo_siga_alt)).replace('\\', '/')
        elif request is not None:
            logo_siga_path = request.build_absolute_uri('/static/images/logo-siga-gab.png')

    return {
        'nome_instituicao': nome_instituicao,
        'brasao_path': brasao_path,
        'logo_conta_path': logo_conta_path,
        'logo_siga_path': logo_siga_path,
    }


def queryset_atendimentos_relatorio(user, request):
    """Aplica permissões e query params comuns aos relatórios de atendimento."""
    queryset = Atendimento.objects.all()

    if user.is_superuser:
        pass
    elif is_in_group(user, 'Recepção'):
        if hasattr(user, 'perfil'):
            queryset = queryset.filter(conta__in=user.perfil.contas.all())
        else:
            return Atendimento.objects.none()
    elif hasattr(user, 'perfil'):
        queryset = queryset.filter(conta__in=user.perfil.contas.all()).filter(
            Q(responsavel=user) | Q(responsavel__isnull=True) | Q(responsaveis_compartilhados=user)
        ).distinct()
    else:
        return Atendimento.objects.none()

    status = _query_param(request, 'status')
    if status:
        queryset = queryset.filter(status=status)

    conta_id = _query_param(request, 'conta_id')
    if conta_id:
        queryset = queryset.filter(conta_id=conta_id)

    assunto_id = _query_param(request, 'assunto_id')
    if assunto_id:
        queryset = queryset.filter(assunto_id=assunto_id)

    responsavel_ids_str = _query_param(request, 'responsavel_ids')
    if responsavel_ids_str:
        try:
            responsavel_ids = [int(x.strip()) for x in responsavel_ids_str.split(',') if x.strip()]
            if responsavel_ids:
                queryset = queryset.filter(responsavel_id__in=responsavel_ids)
        except (ValueError, AttributeError):
            pass

    queryset = aplicar_filtro_periodo_em_queryset(queryset, request)
    queryset = aplicar_filtro_perfil_municipe(queryset, request)

    return queryset


def big_numbers_por_status(queryset):
    status_counts = queryset.values('status').annotate(total=Count('id'))
    big_numbers = {
        'total': queryset.count(),
        'aberto': 0,
        'em_analise': 0,
        'encaminhado': 0,
        'concluido': 0,
        'arquivado': 0,
    }
    status_map = {
        'ABERTO': 'aberto',
        'EM_ANALISE': 'em_analise',
        'ENCAMINHADO': 'encaminhado',
        'CONCLUIDO': 'concluido',
        'ARQUIVADO': 'arquivado',
    }
    for item in status_counts:
        key = status_map.get(item['status'])
        if key:
            big_numbers[key] = item['total']
    return big_numbers


def big_numbers_por_assunto(queryset, top=8):
    rows = (
        queryset.values('assunto__nome')
        .annotate(total=Count('id'))
        .order_by('-total')
    )
    result = []
    for row in rows[:top]:
        result.append({
            'nome': row['assunto__nome'] or 'SEM ASSUNTO',
            'total': row['total'],
        })
    return result


def anotar_linha_cargo_orgao(atendimentos, request=None):
    """Anexa `linha_cargo_orgao` em cada atendimento para templates PDF/HTML."""
    categoria_ids = []
    cargos_filtro = []
    if request is not None:
        categoria_ids = parse_categoria_ids_from_request(request)
        from .reporting import _query_params_list
        cargos_filtro = [
            normalizar_cargo(c)
            for c in _query_params_list(request, 'cargo')
            if normalizar_cargo(c)
        ]

    for atendimento in atendimentos:
        perfis_qs = PerfilMunicipe.objects.filter(
            municipe_id=atendimento.municipe_id,
            conta_id=atendimento.conta_id,
            ativo=True,
        ).select_related('categoria')
        if categoria_ids:
            perfis_qs = perfis_qs.filter(categoria_id__in=categoria_ids)
        if cargos_filtro:
            cargo_q = Q()
            for cargo in cargos_filtro:
                cargo_q |= Q(cargo__iexact=cargo)
            perfis_qs = perfis_qs.filter(cargo_q)

        perfis = list(perfis_qs.order_by('-id'))
        if perfis:
            linhas = linhas_cargo_orgao_de_perfis(perfis)
            atendimento.linha_cargo_orgao = '; '.join(linhas) if linhas else '—'
            continue

        cargo = (getattr(atendimento.municipe, 'cargo', None) or '').strip()
        orgao = (getattr(atendimento.municipe, 'orgao', None) or '').strip()
        partes = [p for p in (cargo, orgao) if p]
        atendimento.linha_cargo_orgao = ' · '.join(partes) if partes else '—'


def serializar_atendimentos_por_assunto(queryset, top=20):
    return big_numbers_por_assunto(queryset, top=top)
