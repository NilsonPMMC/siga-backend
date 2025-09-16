# etiquetas/admin.py

from django.contrib import admin
from .models import EtiquetaTemplate, GeracaoEtiqueta

@admin.register(EtiquetaTemplate)
class EtiquetaTemplateAdmin(admin.ModelAdmin):
    list_display = ('nome', 'etiquetas_por_pagina', 'descricao')
    search_fields = ('nome', 'descricao')

@admin.register(GeracaoEtiqueta)
class GeracaoEtiquetaAdmin(admin.ModelAdmin):
    list_display = ('template', 'criado_por', 'data_criacao', 'posicao_inicial')
    list_filter = ('template', 'criado_por', 'data_criacao')
    readonly_fields = ('template', 'contatos_selecionados', 'posicao_inicial', 'criado_por', 'data_criacao')

    def has_add_permission(self, request):
        # Ninguém deve criar registros de geração manualmente, só via sistema.
        return False