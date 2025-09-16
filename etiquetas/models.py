# etiquetas/models.py

from django.db import models
from django.contrib.auth.models import User

class UppercaseFieldsMixin:
    UPPERCASE_EXCEPTIONS = ('template_html')

    def save(self, *args, **kwargs):
        for field in self._meta.fields:
            if field.name not in self.UPPERCASE_EXCEPTIONS:
                if isinstance(field, (models.CharField, models.TextField)) and getattr(self, field.name):
                    setattr(self, field.name, getattr(self, field.name).upper())
        super().save(*args, **kwargs)

class EtiquetaTemplate(UppercaseFieldsMixin, models.Model):
    """
    Armazena os modelos de etiqueta em HTML.
    Cada registro aqui é um formato de folha de etiqueta (Ex: Pimaco 6180).
    """
    nome = models.CharField(
        max_length=100, 
        unique=True, 
        verbose_name="Nome do Modelo",
        help_text="Ex: Pimaco 6180 (10 por folha)"
    )
    descricao = models.TextField(
        blank=True, 
        null=True, 
        verbose_name="Descrição",
        help_text="Detalhes sobre o uso ou tipo de papel deste modelo."
    )
    template_html = models.TextField(
        verbose_name="Código HTML do Template",
        help_text="Use placeholders como {{ nome_completo }}, {{ tratamento }}, {{ endereco.logradouro }}, etc."
    )
    etiquetas_por_pagina = models.PositiveIntegerField(
        default=1, # Um valor padrão seguro
        verbose_name="Itens por Página",
        help_text="Quantos itens (etiquetas/envelopes) cabem em uma única página deste modelo."
    )
    
    class Meta:
        verbose_name = "Modelo de Etiqueta"
        verbose_name_plural = "Modelos de Etiqueta"
        ordering = ['nome']

    def __str__(self):
        return self.nome

class GeracaoEtiqueta(models.Model):
    """
    Registra um lote de etiquetas que foi preparado para impressão.
    Isso serve como um histórico e armazena os dados personalizados.
    """
    template = models.ForeignKey(
        EtiquetaTemplate, 
        on_delete=models.PROTECT,
        verbose_name="Modelo Utilizado"
    )
    # Aqui está a chave da personalização: guardamos uma "foto" dos dados
    # usados na hora da geração, sem alterar o cadastro original do munícipe.
    contatos_selecionados = models.JSONField(
        verbose_name="Dados dos Contatos para Impressão"
    )
    posicao_inicial = models.PositiveIntegerField(
        default=1,
        verbose_name="Posição de Início na Folha",
        help_text="O número da etiqueta na folha onde a impressão deve começar."
    )
    criado_por = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name="etiquetas_geradas"
    )
    data_criacao = models.DateTimeField(
        auto_now_add=True, 
        verbose_name="Data da Geração"
    )

    class Meta:
        verbose_name = "Geração de Etiqueta"
        verbose_name_plural = "Gerações de Etiquetas"
        ordering = ['-data_criacao']

    def __str__(self):
        return f"Geração em {self.data_criacao.strftime('%d/%m/%Y %H:%M')} por {self.criado_por.username}"