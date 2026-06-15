from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from atendimentos.views import GerarPdfAtendimentosView


def _usuario_sistema():
    user = (
        get_user_model()
        .objects.filter(is_superuser=True, is_active=True)
        .order_by("id")
        .first()
    )
    if not user:
        raise RuntimeError(
            "Nenhum superusuário ativo encontrado para gerar os relatórios PDF."
        )
    return user


def _extrair_pdf(response, rotulo):
    if response.status_code != 200:
        detail = getattr(response, "data", None) or response.content[:500]
        raise RuntimeError(
            f"Falha ao gerar {rotulo} (HTTP {response.status_code}): {detail}"
        )
    return response.content


def _request_autenticada(method, path, params):
    user = _usuario_sistema()
    request = getattr(APIRequestFactory(), method)(path, params)
    force_authenticate(request, user=user)
    return request


def gerar_pdf_atendimentos(conta_id, data_referencia):
    data_str = data_referencia.strftime("%Y-%m-%d")
    request = _request_autenticada(
        "get",
        "/api/relatorios/atendimentos/pdf/",
        {
            "data_inicio": data_str,
            "data_fim": data_str,
            "conta_id": str(conta_id),
        },
    )
    response = GerarPdfAtendimentosView.as_view()(request)
    return _extrair_pdf(response, "relatório de atendimentos")
