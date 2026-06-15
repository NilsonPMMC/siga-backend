from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import Evento, Convidado, EventoChecklist, Comunicacao, Destinatario, LogDeEnvio, ListaPresenca, ChecklistItem, EventoChecklistItemStatus, MailingList, EmailSupressao
from .checklist_security import validar_observacoes_checklist, validar_nome_item_mestre_checklist
from atendimentos.models import Municipe, PerfilMunicipe 

class MunicipeForConvidadoSerializer(serializers.ModelSerializer):
    perfis = serializers.SerializerMethodField()

    def get_perfis(self, obj):
        """
        Retorna perfis (cargo/instituicao) filtrados pela conta do contexto.
        Isso substitui o legado cargo/orgao do Municipe no frontend.
        """
        conta_id = self.context.get("conta_id")
        perfis_qs = getattr(obj, "perfis", None)
        if perfis_qs is None:
            return []

        if conta_id:
            # Sempre consulta por conta_id para garantir que nunca vaze perfil de outra conta.
            from atendimentos.models import PerfilMunicipe
            perfis = list(
                PerfilMunicipe.objects.filter(
                    municipe_id=getattr(obj, "id", None),
                    conta_id=conta_id,
                    ativo=True,
                ).select_related("conta")
            )
        else:
            perfis = list(perfis_qs.all()) if perfis_qs is not None else []

        perfis = [p for p in perfis if getattr(p, "ativo", True)]
        return [
            {
                "id": p.id,
                "conta": getattr(p, "conta_id", None),
                "cargo": p.cargo,
                "instituicao": p.instituicao,
                "departamento": p.departamento,
                "tratamento": p.tratamento,
            }
            for p in perfis
        ]

    class Meta:
        model = Municipe
        fields = ['id', 'nome_completo', 'nome_de_guerra', 'cargo', 'orgao', 'telefones', 'emails', 'perfis']


class PerfilMunicipeConvidadoSerializer(serializers.ModelSerializer):
    """Perfil no convite (cargo/órgão) + munícipe aninhado para exibição."""
    conta_nome = serializers.CharField(source='conta.nome', read_only=True)
    municipe = MunicipeForConvidadoSerializer(read_only=True)

    class Meta:
        model = PerfilMunicipe
        fields = ['id', 'conta', 'conta_nome', 'cargo', 'instituicao', 'departamento', 'tratamento', 'municipe']


class ConvidadoSerializer(serializers.ModelSerializer):
    perfil = PerfilMunicipeConvidadoSerializer(read_only=True)
    perfil_id = serializers.PrimaryKeyRelatedField(
        queryset=PerfilMunicipe.objects.filter(ativo=True).select_related('municipe', 'conta'),
        source='perfil',
        write_only=True
    )

    class Meta:
        model = Convidado
        fields = ['id', 'evento', 'perfil', 'perfil_id', 'municipe', 'status', 'data_checkin', 'ordem']
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=Convidado.objects.all(),
                fields=('evento', 'perfil'),
                message="Este perfil já foi convidado para o evento."
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
    emails_suprimidos_count = serializers.SerializerMethodField()

    def get_emails_suprimidos_count(self, obj):
        """
        Conta quantos logs de envio desta comunicação falharam por supressão.
        """
        return LogDeEnvio.objects.filter(
            comunicacao=obj,
            status='falha',
            detalhe_erro__icontains='suprimido'
        ).count()

    class Meta:
        model = Comunicacao
        fields = [
            'id', 'evento', 'evento_id', 'titulo', 'descricao', 'arte', 'anexo', 'status',
            'data_criacao', 'data_envio', 'grupos_inclusao', 'emails_suprimidos_count',
            'remover_arte', 'remover_anexo'
        ]
        read_only_fields = ['evento', 'grupos_inclusao', 'data_criacao', 'data_envio', 'emails_suprimidos_count']
        
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

class MunicipeDestinatarioListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Municipe
        fields = ['id', 'nome_completo', 'nome_de_guerra', 'cargo', 'emails']


class DestinatarioListSerializer(serializers.ModelSerializer):
    municipe = MunicipeDestinatarioListSerializer(read_only=True)

    class Meta:
        model = Destinatario
        fields = ['id', 'comunicacao', 'municipe']


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
        fields = ['id', 'nome_completo', 'telefone', 'email', 'instituicao_orgao', 'data_registro', 'municipe_id']

class ChecklistItemSerializer(serializers.ModelSerializer):
    def validate_nome(self, value):
        try:
            return validar_nome_item_mestre_checklist(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)

    class Meta:
        model = ChecklistItem
        fields = ['id', 'nome']

class EventoChecklistItemStatusSerializer(serializers.ModelSerializer):
    item_mestre = ChecklistItemSerializer(read_only=True)
    item_mestre_id = serializers.PrimaryKeyRelatedField(
        queryset=ChecklistItem.objects.all(),
        source='item_mestre',
        write_only=True
    )

    def validate_observacoes(self, value):
        try:
            return validar_observacoes_checklist(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)

    class Meta:
        model = EventoChecklistItemStatus
        fields = ['id', 'evento_checklist', 'item_mestre', 'item_mestre_id', 'concluido', 'observacoes']
        extra_kwargs = {
            'evento_checklist': {'write_only': True}
        }

class EventoChecklistSerializer(serializers.ModelSerializer):
    itens_status = EventoChecklistItemStatusSerializer(many=True, read_only=True)
    evento = EventoSimpleSerializer(read_only=True)

    class Meta:
        model = EventoChecklist
        fields = ['id', 'evento', 'nome_responsavel', 'token', 'token_usado', 'data_envio', 'itens_status']

class EventoSerializer(serializers.ModelSerializer):
    checklist = EventoChecklistSerializer(read_only=True)

    class Meta:
        model = Evento
        fields = ['id', 'conta', 'nome', 'descricao', 'data_evento', 'local', 'status', 'ativo', 'checklist', ]
        read_only_fields = ['conta']

class MailingListSerializer(serializers.ModelSerializer):
    """
    Serializer para listar e criar Mailing Lists.
    """
    total_municipes = serializers.IntegerField(source='municipes.count', read_only=True)

    class Meta:
        model = MailingList
        fields = ['id', 'nome', 'conta', 'criado_em', 'total_municipes']
        read_only_fields = ['conta']


class EmailSupressaoSerializer(serializers.ModelSerializer):
    conta_nome = serializers.CharField(source='conta.nome', read_only=True, allow_null=True)
    criado_por_nome = serializers.CharField(source='criado_por.get_full_name', read_only=True, allow_null=True)
    atualizado_por_nome = serializers.CharField(source='atualizado_por.get_full_name', read_only=True, allow_null=True)
    motivo_display = serializers.CharField(source='get_motivo_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    origem_display = serializers.CharField(source='get_origem_display', read_only=True)
    municipes_relacionados = serializers.SerializerMethodField()
    
    def get_municipes_relacionados(self, obj):
        """Retorna lista de municipes que possuem este e-mail."""
        municipes = obj.get_municipes_relacionados()
        return [
            {
                'id': m.id,
                'nome_completo': m.nome_completo,
                'nome_de_guerra': m.nome_de_guerra,
            }
            for m in municipes[:5]  # Limita a 5 para não sobrecarregar
        ]

    class Meta:
        model = EmailSupressao
        fields = [
            'id', 'email', 'status', 'status_display', 'motivo', 'motivo_display',
            'origem', 'origem_display', 'primeira_ocorrencia', 'ultima_ocorrencia',
            'ocorrencias', 'conta', 'conta_nome', 'observacao',
            'criado_por', 'criado_por_nome', 'atualizado_por', 'atualizado_por_nome',
            'criado_em', 'atualizado_em', 'municipes_relacionados'
        ]
        read_only_fields = [
            'primeira_ocorrencia', 'ultima_ocorrencia', 'criado_em', 'atualizado_em',
            'criado_por', 'atualizado_por', 'municipes_relacionados'
        ]


class EmailSupressaoStatsSerializer(serializers.Serializer):
    """Serializer para estatísticas de supressão."""
    total_suprimidos = serializers.IntegerField()
    total_liberados = serializers.IntegerField()
    total_geral = serializers.IntegerField()
    novos_ultima_semana = serializers.IntegerField()
    top_motivos = serializers.ListField(child=serializers.DictField())