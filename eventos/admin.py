from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

# Importa todos os modelos do app de uma vez
from .models import (
    Evento, 
    Convidado, 
    ListaPresenca, 
    ChecklistItem, 
    EventoChecklist, 
    EventoChecklistItemStatus,
    Comunicacao,
    Destinatario,
    MailingList
)

# -----------------------------------------------------------------------------
# 1. DEFINIÇÃO DE TODAS AS CLASSES "INLINE" PRIMEIRO
# -----------------------------------------------------------------------------

class EventoChecklistItemStatusInline(admin.TabularInline):
    model = EventoChecklistItemStatus
    fields = ('item_mestre', 'concluido', 'observacoes')
    readonly_fields = ('item_mestre',)
    extra = 0
    can_delete = False
    def has_add_permission(self, request, obj=None):
        return False

class EventoChecklistInline(admin.StackedInline):
    model = EventoChecklist
    extra = 0
    fields = ('nome_responsavel',)
    max_num = 1
    can_delete = False

class ComunicacaoInline(admin.TabularInline):
    model = Comunicacao
    extra = 1
    fields = ('titulo', 'status', 'data_envio')
    readonly_fields = ('data_envio',)
    show_change_link = True

class ConvidadoInline(admin.TabularInline):
    model = Convidado
    raw_id_fields = ('municipe',)
    extra = 1
    autocomplete_fields = ('municipe',)

# -----------------------------------------------------------------------------
# 2. DEFINIÇÃO DAS CLASSES "ADMIN" PRINCIPAIS
# -----------------------------------------------------------------------------

@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'conta', 'status', 'ativo', 'link_para_checklist')
    readonly_fields = ('link_para_checklist',)
    search_fields = ('nome', 'descricao', 'conta__nome')
    list_filter = ('conta', 'status', 'ativo')

    # Agora que todas as Inlines estão definidas acima, esta lista funcionará
    inlines = [
        EventoChecklistInline, 
        ComunicacaoInline, 
        ConvidadoInline
    ]

    fieldsets = (
        (None, {
            'fields': ('conta', 'nome', 'descricao', 'local')
        }),
        ('Detalhes do Evento', {
            'fields': ('data_evento', ('status', 'ativo'))
        }),
    )

    def link_para_checklist(self, obj):
        checklist = EventoChecklist.objects.filter(evento=obj).first()
        if checklist:
            url = reverse('admin:eventos_eventochecklist_change', args=[checklist.pk])
            return format_html('<a href="{}">Ver Checklist</a>', url)
        return "Nenhum checklist associado."
    link_para_checklist.short_description = 'Checklist'


@admin.register(Convidado)
class ConvidadoAdmin(admin.ModelAdmin):
    list_display = ('evento', 'municipe', 'status', 'data_checkin')
    list_filter = ('status', 'evento')
    search_fields = ('municipe__nome_completo', 'evento__nome')
    autocomplete_fields = ('municipe', 'evento')


@admin.register(ChecklistItem)
class ChecklistItemAdmin(admin.ModelAdmin):
    list_display = ('nome',)
    search_fields = ('nome',)


@admin.register(EventoChecklist)
class EventoChecklistAdmin(admin.ModelAdmin):
    list_display = ('evento', 'nome_responsavel', 'token_usado', 'data_envio', 'link_publico')
    readonly_fields = ('evento', 'token', 'nome_responsavel', 'token_usado', 'data_envio', 'link_publico')
    inlines = [EventoChecklistItemStatusInline]
    
    def link_publico(self, obj):
        base_url_frontend = "https://gabinete.mogidascruzes.sp.gov.br"
        url_completa = f"{base_url_frontend}/public/checklist/{obj.token}"
        return format_html('<a href="{0}" target="_blank">{0}</a>', url_completa)
    link_publico.short_description = 'Link Público para o Formulário'


@admin.register(ListaPresenca)
class ListaPresencaAdmin(admin.ModelAdmin):
    list_display = ('evento', 'nome_completo', 'telefone', 'data_registro')
    list_filter = ('evento',)
    search_fields = ('nome_completo', 'evento__nome')
    readonly_fields = ('evento', 'municipe', 'nome_completo', 'telefone', 'email', 'instituicao_orgao', 'data_registro')

    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False

@admin.register(Comunicacao)
class ComunicacaoAdmin(admin.ModelAdmin):
    """
    Esta classe faz com que 'Comunicações' apareça no menu principal do admin.
    """
    list_display = ('titulo', 'evento', 'status', 'data_criacao', 'data_envio')
    list_filter = ('status', 'evento__conta')
    search_fields = ('titulo', 'descricao', 'evento__nome')
    list_editable = ('status',)
    readonly_fields = ('data_criacao', 'data_envio')

@admin.register(Destinatario)
class DestinatarioAdmin(admin.ModelAdmin):
    list_display = ('municipe', 'comunicacao')
    search_fields = ('municipe__nome_completo', 'comunicacao__titulo')
    autocomplete_fields = ('municipe', 'comunicacao')
    list_filter = ('comunicacao__evento',)

@admin.register(MailingList)
class MailingListAdmin(admin.ModelAdmin):
    list_display = ('nome', 'conta', 'total_municipes')
    list_filter = ('conta',)
    search_fields = ('nome',)
    filter_horizontal = ('municipes',) # Facilita a seleção de múltiplos contatos

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('municipes')

    def total_municipes(self, obj):
        return obj.municipes.count()
    total_municipes.short_description = 'Nº de Contatos'