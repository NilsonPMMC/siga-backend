"""Utilitários de agregação para o Django Admin (Épico 9)."""
from django.db.models import Count


def resumo_categorias_municipes(queryset):
    """Contagem de munícipes por categoria no queryset filtrado do changelist."""
    rows = (
        queryset.filter(perfis__categoria__isnull=False)
        .values('perfis__categoria__nome')
        .annotate(total=Count('pk', distinct=True))
        .order_by('-total', 'perfis__categoria__nome')
    )
    return [
        {'nome': row['perfis__categoria__nome'], 'total': row['total']}
        for row in rows
        if row['perfis__categoria__nome']
    ]


def injetar_resumo_changelist(response, chave, itens):
    """Anexa lista de resumo ao contexto de um TemplateResponse do changelist."""
    if not hasattr(response, 'context_data') or response.context_data is None:
        return response
    if 'cl' not in response.context_data:
        return response
    response.context_data[chave] = itens
    return response
