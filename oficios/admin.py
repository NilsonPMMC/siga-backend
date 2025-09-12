from django.contrib import admin
from .models import Oficio

@admin.register(Oficio)
class OficioAdmin(admin.ModelAdmin):
    """
    Configuração da interface de administração para o modelo Oficio.
    """
    list_display = (
        'numero',
        'assunto',
        'destinatario_nome',
        'conta',
        'data_documento',
        'criado_por',
    )
    search_fields = (
        'numero',
        'assunto',
        'destinatario_nome',
        'destinatario_cargo',
        'destinatario_orgao',
        'corpo',
    )
    list_filter = (
        'conta',
        'ano',
        'data_documento',
    )
    readonly_fields = (
        'numero',
        'ano',
        'criado_por',
        'data_criacao',
    )
    fieldsets = (
        ('Informações do Ofício', {
            'fields': ('numero', 'ano', 'assunto', 'data_documento', 'conta')
        }),
        ('Informações do Destinatário', {
            'fields': ('destinatario_tratamento','destinatario_nome', 'destinatario_cargo', 'destinatario_orgao')
        }),
        ('Conteúdo', {
            'fields': ('corpo',)
        }),
        ('Controle', {
            'fields': ('criado_por', 'data_criacao')
        }),
    )

    def get_queryset(self, request):
        # Otimiza a consulta para evitar múltiplas buscas ao banco de dados
        return super().get_queryset(request).select_related('conta', 'criado_por')