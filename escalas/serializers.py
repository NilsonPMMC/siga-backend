from rest_framework import serializers
from .models import EscalaPeriodo, EscalaRegistro, ContatoEmergencia
from atendimentos.models import Conta, Municipe # Aqui podemos importar normal

class EscalaPeriodoSerializer(serializers.ModelSerializer):
    status_aberto = serializers.ReadOnlyField(source='is_aberto')
    
    class Meta:
        model = EscalaPeriodo
        fields = '__all__'

class EscalaRegistroSerializer(serializers.ModelSerializer):
    # Campos Read-Only para exibir nomes bonitos no Frontend
    nome_conta = serializers.ReadOnlyField(source='conta.nome')
    nome_servidor = serializers.ReadOnlyField(source='servidor.nome_completo')
    foto_servidor = serializers.SerializerMethodField()

    class Meta:
        model = EscalaRegistro
        fields = [
            'id', 'periodo', 'conta', 'nome_conta', 
            'servidor', 'nome_servidor', 'foto_servidor',
            'telefone_plantao', 'cargo_funcao_plantao', 
            'observacao', 'registrado_por', 'data_registro'
        ]
        read_only_fields = ['registrado_por', 'data_registro']

    def get_foto_servidor(self, obj):
        if obj.servidor.foto:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.servidor.foto.url)
        return None
    
class ContatoEmergenciaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContatoEmergencia
        fields = ['id', 'nome', 'telefone', 'descricao']