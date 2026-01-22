from django.contrib import admin
from .models import EscalaPeriodo, EscalaRegistro, ContatoEmergencia

@admin.register(EscalaPeriodo)
class EscalaPeriodoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'data_inicio', 'data_fim', 'data_limite_preenchimento', 'status_calculado', 'ativo')
    list_filter = ('ativo', 'data_inicio')
    search_fields = ('nome',)
    ordering = ('-data_inicio',)
    
    fieldsets = (
        ('Dados do Período', {
            'fields': ('nome', 'ativo')
        }),
        ('Datas', {
            'fields': (('data_inicio', 'data_fim'), 'data_limite_preenchimento')
        }),
    )

    def status_calculado(self, obj):
        """Mostra visualmente se está aberto para edição"""
        return "Aberto" if obj.is_aberto else "Fechado"
    status_calculado.short_description = "Status Preenchimento"


@admin.register(EscalaRegistro)
class EscalaRegistroAdmin(admin.ModelAdmin):
    list_display = ('periodo', 'conta_nome', 'servidor_nome', 'telefone_plantao', 'cargo_funcao_plantao')
    list_filter = ('periodo', 'conta') # Filtro lateral para ver "Quem trabalhou no Carnaval?"
    search_fields = ('servidor__nome_completo', 'servidor__cpf', 'conta__nome', 'telefone_plantao')
    autocomplete_fields = ['conta', 'servidor']
    readonly_fields = ('registrado_por', 'data_registro')

    def save_model(self, request, obj, form, change):
        # Garante que salva quem registrou, mesmo pelo Admin
        if not obj.pk:
            obj.registrado_por = request.user
        super().save_model(request, obj, form, change)

    # Helpers para exibir nomes amigáveis na listagem
    def conta_nome(self, obj):
        return obj.conta.nome
    conta_nome.short_description = 'Secretaria/Órgão'
    
    def servidor_nome(self, obj):
        return obj.servidor.nome_completo
    servidor_nome.short_description = 'Plantonista'

@admin.register(ContatoEmergencia)
class ContatoEmergenciaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'telefone', 'descricao', 'ordem', 'ativo')
    list_editable = ('ordem', 'ativo') # Permite ordenar rápido sem abrir o registro
    search_fields = ('nome', 'descricao')