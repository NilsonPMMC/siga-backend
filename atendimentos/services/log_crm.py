"""Registro centralizado de logs de auditoria do CRM (Épico 11)."""
import os
import threading
from contextlib import contextmanager

from django.contrib.auth.models import User

from ..models import Conta, LogDeAtividade
from ..request_middleware import get_current_request, get_current_user

_suppress = threading.local()
_snapshots = {}


def is_crm_log_suppressed():
    return bool(getattr(_suppress, 'active', False))


@contextmanager
def suppress_crm_logs():
    """Evita logs automáticos durante mesclagem/unificação de duplicatas."""
    prev = getattr(_suppress, 'active', False)
    _suppress.active = True
    try:
        yield
    finally:
        _suppress.active = prev


def _serialize_value(value):
    if value is None or value == '':
        return None
    if hasattr(value, 'pk'):
        return value.pk
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, dict)):
        return value
    return str(value)


def snapshot_instance(model_label, instance, field_names):
    """Guarda estado anterior (do banco) para diff em post_save."""
    if not instance.pk:
        return
    try:
        old_instance = instance.__class__.objects.get(pk=instance.pk)
    except instance.__class__.DoesNotExist:
        return
    data = {}
    for name in field_names:
        if name.endswith('_id'):
            data[name] = getattr(old_instance, name, None)
        else:
            val = getattr(old_instance, name, None)
            if hasattr(val, 'pk'):
                data[f'{name}_id'] = val.pk
            else:
                data[name] = val
    _snapshots[(model_label, instance.pk)] = data


def pop_snapshot(model_label, pk):
    return _snapshots.pop((model_label, pk), None)


def diff_fields(snapshot, instance, field_names):
    if not snapshot:
        return {}
    changes = {}
    for name in field_names:
        key = f'{name}_id' if not name.endswith('_id') and hasattr(getattr(instance, name, None), 'pk') else name
        old = snapshot.get(key, snapshot.get(name))
        if name.endswith('_id'):
            new = getattr(instance, name, None)
        else:
            attr = getattr(instance, name, None)
            new = attr.pk if hasattr(attr, 'pk') else attr
        if _serialize_value(old) != _serialize_value(new):
            changes[name] = {'antes': _serialize_value(old), 'depois': _serialize_value(new)}
    return changes


def _request_query_param(request, key):
    if hasattr(request, 'query_params'):
        return request.query_params.get(key)
    return request.GET.get(key)


def _request_body_param(request, key):
    if hasattr(request, 'data'):
        val = request.data.get(key)
        if val is not None:
            return val
    try:
        return request.POST.get(key)
    except Exception:
        return None


def resolve_conta_from_request():
    request = get_current_request()
    if not request:
        return None
    raw = (
        _request_query_param(request, 'conta_id')
        or _request_body_param(request, 'conta_id')
        or _request_body_param(request, 'conta')
    )
    if raw is not None:
        try:
            return Conta.objects.filter(pk=int(raw)).first()
        except (TypeError, ValueError):
            pass
    user = get_current_user()
    if user and hasattr(user, 'perfil'):
        return user.perfil.contas.order_by('id').first()
    return None


def resolve_conta_for_municipe(municipe):
    perfil = municipe.perfis.select_related('conta').order_by('id').first()
    if perfil:
        return perfil.conta
    return municipe.contas.order_by('id').first()


def registrar_log_crm(
    acao,
    detalhes,
    content_object=None,
    conta=None,
    payload=None,
    usuario=None,
    object_id=None,
):
    if os.environ.get('SILENCE_SIGNALS') or is_crm_log_suppressed():
        return None

    user = usuario or get_current_user() or User.objects.filter(is_superuser=True).first()
    ct = None
    oid = object_id
    if content_object is not None:
        ct = content_object._meta.model
        oid = content_object.pk

    kwargs = {
        'usuario': user,
        'acao': acao,
        'detalhes': detalhes,
        'conta': conta,
        'payload': payload or {},
    }
    if content_object is not None:
        kwargs['content_object'] = content_object
    else:
        kwargs['content_type'] = None
        kwargs['object_id'] = oid or 0

    return LogDeAtividade.objects.create(**kwargs)


def registrar_log_mesclagem_municipes(
    request,
    principal,
    duplicado_id,
    duplicado_nome,
    links_migrados=None,
    transferidos=None,
):
    conta = resolve_conta_from_request() or resolve_conta_for_municipe(principal)
    payload = {
        'id_principal': principal.pk,
        'id_duplicado': duplicado_id,
        'nome_principal': principal.nome_completo,
        'nome_duplicado': duplicado_nome,
    }
    if links_migrados is not None:
        payload['links_migrados'] = links_migrados
    if transferidos:
        payload['transferidos'] = transferidos

    detalhes = (
        f"Mesclagem: munícipe duplicado #{duplicado_id} ({duplicado_nome}) "
        f"unificado em #{principal.pk} ({principal.nome_completo})."
    )
    return registrar_log_crm(
        acao='MESCLAGEM',
        detalhes=detalhes,
        content_object=principal,
        conta=conta,
        payload=payload,
        usuario=request.user if request else None,
    )
