from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Oficio
from atendimentos.models import Conta

class OficioSerializer(serializers.ModelSerializer):
    """
    Serializer para listar e detalhar os Ofícios.
    """
    # Campos de leitura para facilitar a exibição no frontend,
    # buscando os nomes dos objetos relacionados.
    conta_nome = serializers.CharField(source='conta.nome', read_only=True)
    criado_por_nome = serializers.CharField(source='criado_por.get_full_name', read_only=True)

    class Meta:
        model = Oficio
        # Inclui todos os campos do modelo no serializer
        fields = '__all__'
        
        # Define campos que não devem ser exigidos na criação/edição via API,
        # pois são preenchidos automaticamente pelo backend.
        read_only_fields = (
            'numero', 
            'ano', 
            'criado_por', 
            'data_criacao',
            # Campos extras que adicionamos
            'conta_nome',
            'criado_por_nome'
        )

    def validate(self, data):
        """
        Validações personalizadas para o ofício.
        """
        # Garante que a conta (secretaria) seja fornecida, a menos que seja uma atualização parcial (PATCH)
        if not self.partial and 'conta' not in data:
            raise serializers.ValidationError({"conta": "A secretaria/gabinete é obrigatória."})

        # Impede que o corpo do ofício seja salvo em branco
        if 'corpo' in data and not data.get('corpo').strip():
            raise serializers.ValidationError({"corpo": "O corpo do ofício não pode estar vazio."})

        return data