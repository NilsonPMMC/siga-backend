# etiquetas/serializers.py

from rest_framework import serializers
from .models import EtiquetaTemplate, GeracaoEtiqueta
from atendimentos.serializers import UserSerializer  # Reutilizamos o serializer de usuário já existente

class EtiquetaTemplateSerializer(serializers.ModelSerializer):
    """
    Serializer para o modelo de template de etiqueta.
    Usado para criar, listar e editar os templates.
    """
    class Meta:
        model = EtiquetaTemplate
        fields = '__all__'

class GeracaoEtiquetaSerializer(serializers.ModelSerializer):
    """
    Serializer para registrar e visualizar o histórico de gerações.
    """
    criado_por = UserSerializer(read_only=True)
    template = EtiquetaTemplateSerializer(read_only=True)

    class Meta:
        model = GeracaoEtiqueta
        fields = '__all__'

class GerarEtiquetaRequestSerializer(serializers.Serializer):
    """
    Este serializer não está ligado a um modelo. Ele serve apenas para
    validar os dados que o frontend enviará para o endpoint de geração.
    """
    template_id = serializers.IntegerField(
        required=True,
        help_text="ID do EtiquetaTemplate a ser utilizado."
    )
    posicao_inicial = serializers.IntegerField(
        required=False,
        default=1,
        min_value=1,
        help_text="Posição inicial da impressão na folha."
    )
    contatos = serializers.ListField(
        child=serializers.JSONField(),
        allow_empty=False,
        help_text="Lista de objetos JSON, cada um representando um contato com seus dados."
    )