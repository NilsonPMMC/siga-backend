from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.db.models import Count

from atendimentos.admin_utils import injetar_resumo_changelist

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
    MailingList,
    EmailSupressao
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
    extra = 1
    autocomplete_fields = ('perfil',)

# -----------------------------------------------------------------------------
# 2. DEFINIÇÃO DAS CLASSES "ADMIN" PRINCIPAIS
# -----------------------------------------------------------------------------

def _resumo_eventos_por_conta(queryset):
    rows = (
        queryset.filter(conta__isnull=False)
        .values('conta__nome')
        .annotate(total=Count('pk'))
        .order_by('-total', 'conta__nome')
    )
    return [{'nome': row['conta__nome'], 'total': row['total']} for row in rows]


def _resumo_comunicacoes_por_conta(queryset):
    rows = (
        queryset.filter(evento__conta__isnull=False)
        .values('evento__conta__nome')
        .annotate(total=Count('pk'))
        .order_by('-total', 'evento__conta__nome')
    )
    return [{'nome': row['evento__conta__nome'], 'total': row['total']} for row in rows]


@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    change_list_template = 'admin/eventos/evento/change_list.html'
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

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        try:
            cl = response.context_data['cl']
            resumo = _resumo_eventos_por_conta(cl.queryset)
        except (AttributeError, KeyError, TypeError):
            resumo = []
        return injetar_resumo_changelist(response, 'resumo_contas', resumo)

    def link_para_checklist(self, obj):
        checklist = EventoChecklist.objects.filter(evento=obj).first()
        if checklist:
            url = reverse('admin:eventos_eventochecklist_change', args=[checklist.pk])
            return format_html('<a href="{}">Ver Checklist</a>', url)
        return "Nenhum checklist associado."
    link_para_checklist.short_description = 'Checklist'


@admin.register(Convidado)
class ConvidadoAdmin(admin.ModelAdmin):
    list_display = ('evento', 'perfil', 'status', 'data_checkin')
    list_filter = ('status', 'evento')
    search_fields = ('perfil__municipe__nome_completo', 'evento__nome')
    autocomplete_fields = ('perfil', 'evento')


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
    change_list_template = 'admin/eventos/comunicacao/change_list.html'
    list_display = ('titulo', 'evento', 'status', 'data_criacao', 'data_envio')
    list_filter = ('status', 'evento__conta')
    search_fields = ('titulo', 'descricao', 'evento__nome')
    list_editable = ('status',)
    readonly_fields = ('data_criacao', 'data_envio')

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        try:
            cl = response.context_data['cl']
            resumo = _resumo_comunicacoes_por_conta(cl.queryset)
        except (AttributeError, KeyError, TypeError):
            resumo = []
        return injetar_resumo_changelist(response, 'resumo_contas', resumo)

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


@admin.register(EmailSupressao)
class EmailSupressaoAdmin(admin.ModelAdmin):
    list_display = ('email', 'status', 'motivo', 'ocorrencias', 'conta', 'ultima_ocorrencia', 'criado_por', 'total_contatos')
    list_filter = ('status', 'motivo', 'origem', 'conta')
    search_fields = ('email', 'observacao')
    readonly_fields = ('primeira_ocorrencia', 'ultima_ocorrencia', 'criado_em', 'atualizado_em', 'contatos_vinculados')
    list_editable = ('status',)
    actions = ['liberar_emails', 'ativar_supressao']
    
    fieldsets = (
        ('E-mail e Status', {
            'fields': ('email', 'status', 'motivo', 'origem')
        }),
        ('Estatísticas', {
            'fields': ('ocorrencias', 'primeira_ocorrencia', 'ultima_ocorrencia')
        }),
        ('Contatos Relacionados', {
            'fields': ('contatos_vinculados',),
            'description': 'Contatos (Munícipes) que possuem este e-mail cadastrado'
        }),
        ('Relacionamentos', {
            'fields': ('conta', 'observacao')
        }),
        ('Auditoria', {
            'fields': ('criado_por', 'atualizado_por', 'criado_em', 'atualizado_em'),
            'classes': ('collapse',)
        }),
    )
    
    def total_contatos(self, obj):
        """Exibe quantidade de contatos vinculados na lista."""
        count = obj.get_municipes_relacionados().count()
        if count > 0:
            return f'{count} contato(s)'
        return '-'
    total_contatos.short_description = 'Contatos'
    
    def contatos_vinculados(self, obj):
        """Exibe lista de contatos vinculados com links no detalhe."""
        from django.utils.html import format_html
        from django.urls import reverse
        
        municipes = obj.get_municipes_relacionados()
        
        if not municipes.exists():
            return format_html('<em>Nenhum contato encontrado com este e-mail.</em>')
        
        links = []
        for m in municipes[:10]:  # Limita a 10 para não sobrecarregar
            url = reverse('admin:atendimentos_municipe_change', args=[m.id])
            links.append(
                f'<a href="{url}" target="_blank">{m.nome_completo}</a>'
            )
        
        result = '<br>'.join(links)
        
        if municipes.count() > 10:
            result += f'<br><em>... e mais {municipes.count() - 10} contato(s)</em>'
        
        return format_html(result)
    contatos_vinculados.short_description = 'Contatos com este e-mail'
    
    def liberar_emails(self, request, queryset):
        atualizados = queryset.update(status='liberado', atualizado_por=request.user)
        self.message_user(
            request,
            f'{atualizados} e-mail(s) liberado(s) para envio.',
            level='success'
        )
    liberar_emails.short_description = 'Liberar e-mails selecionados'
    
    def ativar_supressao(self, request, queryset):
        atualizados = queryset.update(status='ativo', atualizado_por=request.user)
        self.message_user(
            request,
            f'{atualizados} e-mail(s) bloqueado(s) para envio.',
            level='warning'
        )
    ativar_supressao.short_description = 'Bloquear e-mails selecionados'
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.criado_por = request.user
        obj.atualizado_por = request.user
        super().save_model(request, obj, form, change)