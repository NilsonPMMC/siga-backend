from rest_framework import serializers
from .models import Evento, Convidado, EventoChecklist, Comunicacao, Destinatario, LogDeEnvio, ListaPresenca, ChecklistItem, EventoChecklistItemStatus
from atendimentos.models import Municipe 

class EventoChecklistSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventoChecklist
        fields = ['token']


class EventoSerializer(serializers.ModelSerializer):
    checklist = EventoChecklistSerializer(read_only=True)

    class Meta:
        model = Evento
        fields = ['id', 'conta', 'nome', 'descricao', 'data_evento', 'local', 'status', 'ativo', 'checklist', ]
        read_only_fields = ['conta']

class MunicipeForConvidadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Municipe
        fields = ['id', 'nome_completo', 'nome_de_guerra', 'cargo', 'orgao', 'telefones', 'emails']

class ConvidadoSerializer(serializers.ModelSerializer):
    municipe = MunicipeForConvidadoSerializer(read_only=True)
    municipe_id = serializers.PrimaryKeyRelatedField(
        queryset=Municipe.objects.all(),
        source='municipe',
        write_only=True
    )

    class Meta:
        model = Convidado
        fields = ['id', 'evento', 'municipe', 'municipe_id', 'status',  'data_checkin']
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=Convidado.objects.all(),
                fields=('evento', 'municipe'), # Uses the actual model fields
                message="Este munícipe já foi convidado para o evento."
            )
        ]

class EventoSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evento
        fields = ['id', 'nome'] 

class ComunicacaoSerializer(serializers.ModelSerializer):
    remover_arte = serializers.BooleanField(write_only=True, required=False)
    remover_anexo = serializers.BooleanField(write_only=True, required=False)
    evento = EventoSimpleSerializer(read_only=True)
    evento_id = serializers.IntegerField(write_only=True, source='evento')

    class Meta:
        model = Comunicacao
        fields = [
            'id', 'evento', 'evento_id', 'titulo', 'descricao', 'arte', 'anexo', 'status', 
            'data_criacao', 'data_envio', 
            'remover_arte', 'remover_anexo'
        ]
        read_only_fields = ['evento']
        
    def create(self, validated_data):
        # Remove as flags que são usadas apenas na atualização (update)
        validated_data.pop('remover_arte', None)
        validated_data.pop('remover_anexo', None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # O seu método 'update' que já criamos continua perfeito
        if validated_data.get('remover_arte', False):
            instance.arte.delete(save=False)
            instance.arte = None
        
        if validated_data.get('remover_anexo', False):
            instance.anexo.delete(save=False)
            instance.anexo = None

        validated_data.pop('remover_arte', None)
        validated_data.pop('remover_anexo', None)

        return super().update(instance, validated_data)

class DestinatarioSerializer(serializers.ModelSerializer):
    # Reutilizamos o serializer de munícipe que já temos para mostrar os detalhes
    municipe = MunicipeForConvidadoSerializer(read_only=True)
    
    # Para a criação, usamos o PrimaryKeyRelatedField que já se provou robusto
    municipe_id = serializers.PrimaryKeyRelatedField(
        queryset=Municipe.objects.all(),
        source='municipe',
        write_only=True
    )

    class Meta:
        model = Destinatario
        fields = ['id', 'comunicacao', 'municipe', 'municipe_id']
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=Destinatario.objects.all(),
                fields=('comunicacao', 'municipe'),
                message="Este contato já está na lista de destinatários."
            )
        ]

class LogDeEnvioSerializer(serializers.ModelSerializer):
    # Traz o nome completo do munícipe para facilitar a exibição no frontend
    destinatario_nome = serializers.CharField(source='destinatario.municipe.nome_completo', read_only=True)

    class Meta:
        model = LogDeEnvio
        fields = ['id', 'destinatario_nome', 'status', 'data_envio', 'detalhe_erro']

class ListaPresencaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ListaPresenca
        # Traz todos os campos que a tabela do frontend vai precisar
        fields = ['id', 'nome_completo', 'telefone', 'email', 'instituicao_orgao', 'data_registro']

class ChecklistItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChecklistItem
        fields = ['id', 'nome']

class EventoChecklistItemStatusSerializer(serializers.ModelSerializer):
    # Traz o nome do item mestre para facilitar a exibição
    item_mestre = ChecklistItemSerializer(read_only=True)

    class Meta:
        model = EventoChecklistItemStatus
        fields = ['id', 'item_mestre', 'concluido', 'observacoes', 'data_conclusao']

class EventoChecklistSerializer(serializers.ModelSerializer):
    # Aninha os itens do checklist dentro do objeto principal
    itens_status = EventoChecklistItemStatusSerializer(many=True, read_only=True)

    class Meta:
        model = EventoChecklist
        fields = ['id', 'evento', 'responsavel_nome', 'token', 'token_usado', 'data_envio', 'itens_status']