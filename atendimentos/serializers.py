from .models import *
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth.models import User
from .permissions import is_in_group, CanEditMunicipeDetails
from datetime import date
from django.utils import timezone

class UserSerializer(serializers.ModelSerializer):
    contas = serializers.PrimaryKeyRelatedField(
        source='perfil.contas', 
        many=True, 
        read_only=True
    )
    groups = serializers.SlugRelatedField(
        many=True,
        read_only=True,
        slug_field='name'
    )
    user_permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'is_superuser', 'first_name', 'last_name', 'contas', 'groups', 'user_permissions']

    def get_user_permissions(self, user):
        if user.is_superuser:
            return []
        return user.get_all_permissions()

class ContaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conta
        fields = '__all__'

class EspacoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Espaco
        fields = '__all__'

class AnexoSerializer(serializers.ModelSerializer):
    usuario_nome = serializers.CharField(source='usuario.username', read_only=True)
    arquivo_url = serializers.SerializerMethodField()

    class Meta:
        model = Anexo
        fields = ['id', 'atendimento', 'arquivo', 'arquivo_url', 'descricao', 'usuario', 'usuario_nome', 'data_upload']
        # Adicione 'atendimento' a esta lista para que ele não seja exigido no POST
        read_only_fields = ['usuario', 'data_upload', 'arquivo_url', 'atendimento']

    def get_arquivo_url(self, obj):
        request = self.context.get('request')
        if obj.arquivo and hasattr(obj.arquivo, 'url'):
            return request.build_absolute_uri(obj.arquivo.url)
        return None

class CategoriaAtendimentoSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaAtendimento
        fields = ['id', 'nome']

class TramitacaoSerializer(serializers.ModelSerializer):
    # Campo para mostrar o nome do usuário em vez do ID
    usuario_nome = serializers.SerializerMethodField()

    class Meta:
        model = Tramitacao
        fields = ['id', 'despacho', 'usuario', 'usuario_nome', 'data_tramitacao']
        # O campo 'usuario' será preenchido automaticamente pela view
        read_only_fields = ['usuario', 'usuario_nome', 'data_tramitacao']

    def get_usuario_nome(self, obj):
        # Se o usuário tiver nome completo, use-o. Senão, use o username.
        full_name = obj.usuario.get_full_name()
        return full_name if full_name else obj.usuario.username

class MunicipeSerializer(serializers.ModelSerializer):
    pode_editar = serializers.SerializerMethodField()
    contas = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Conta.objects.all(),
        required=False
    )
    categoria_nome = serializers.CharField(source='categoria.nome', read_only=True, default='MUNÍCIPE')
    qualidade_dados = serializers.SerializerMethodField()
    alerta_atualizacao = serializers.SerializerMethodField()

    class Meta:
        model = Municipe
        fields = [
            'id', 'nome_completo', 'tratamento', 'nome_de_guerra', 'cpf', 'data_nascimento', 'emails',
            'telefones', 'endereco', 'observacoes', 'cargo', 'orgao',
            'contas',
            'categoria', 'categoria_nome', 'data_cadastro', 'data_atualizacao',
            'qualidade_dados', 'alerta_atualizacao',
            'pode_editar', 'grupo_duplicado'
        ]
        extra_kwargs = {
            'categoria': {'required': False, 'allow_null': True}
        }
    
    def validate_telefones(self, value):
        if not value or not isinstance(value, list) or len(value) == 0:
            raise serializers.ValidationError("É necessário fornecer pelo menos um número de telefone.")
        
        for item in value:
            if not item.get('numero') or not str(item.get('numero')).strip():
                raise serializers.ValidationError("O campo 'número' do telefone não pode estar vazio.")
                
        return value
    
    def to_representation(self, instance):
        """
        Este método controla como os dados são MOSTRADOS.
        Ele pega a saída padrão e substitui os IDs das contas pelos detalhes completos.
        """
        representation = super().to_representation(instance)
        representation['contas'] = ContaSerializer(instance.contas.all(), many=True).data
        return representation

    def get_pode_editar(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        
        user = request.user

        if user.is_superuser:
            return True

        if is_in_group(user, 'Recepção'):
            # Regra 1: O contato DEVE ser da categoria 'Munícipe'.
            if not (obj.categoria is not None and obj.categoria.nome == 'MUNÍCIPE'):
                return False
            
            # Regra 2: Pode editar se o munícipe for público (sem conta vinculada).
            if not obj.contas.exists():
                return True
            
            # Regra 3: Se tiver conta, pode editar se houver uma conta em comum.
            if hasattr(user, 'perfil'):
                user_contas = set(user.perfil.contas.all())
                municipe_contas = set(obj.contas.all())
                return not user_contas.isdisjoint(municipe_contas)
            
            return False

        if is_in_group(user, 'Membro do Gabinete') or is_in_group(user, 'Secretária'):
            if not obj.contas.exists():
                return True
            
            if hasattr(user, 'perfil'):
                user_contas = set(user.perfil.contas.all())
                municipe_contas = set(obj.contas.all())
                return not user_contas.isdisjoint(municipe_contas)
        
        return False

    def get_qualidade_dados(self, obj):
        score = 0
        if obj.cpf and obj.cpf.strip(): score += 1
        if obj.emails and any(e.get('email') for e in obj.emails if isinstance(e, dict)): score += 1
        if obj.telefones: score += 1
        if obj.endereco and obj.endereco.get('cep'): score += 1
        if score == 4: return "Completo"
        if score >= 2: return "Parcial"
        return "Baixo"

    def get_alerta_atualizacao(self, obj):
        if not obj.data_atualizacao: return True
        hoje = timezone.now()
        diferenca = hoje - obj.data_atualizacao
        return diferenca.days > 180

    def to_internal_value(self, data):
        if 'cpf' in data and data['cpf'] == '':
            data['cpf'] = None
        return super().to_internal_value(data)

class AtendimentoSerializer(serializers.ModelSerializer):
    # Seus campos de leitura, que já estavam corretos
    nome_municipe = serializers.CharField(source='municipe.nome_completo', read_only=True)
    nome_conta = serializers.CharField(source='conta.nome', read_only=True)
    tramitacoes = TramitacaoSerializer(many=True, read_only=True)
    categorias = CategoriaAtendimentoSerializer(many=True, read_only=True)
    anexos = AnexoSerializer(many=True, read_only=True)
    responsavel_obj = UserSerializer(source='responsavel', read_only=True)

    responsavel = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), write_only=True, required=False, allow_null=True
    )

    # Seu campo de escrita, que também estava correto
    categorias_ids = serializers.PrimaryKeyRelatedField(
        many=True, queryset=CategoriaAtendimento.objects.all(), source='categorias', write_only=True, required=False
    )

    responsavel_nome = serializers.SerializerMethodField()

    class Meta:
        model = Atendimento
        fields = [
            'id', 'protocolo', 'titulo', 'descricao', 'status', 'conta', 'nome_conta',
            'municipe', 'nome_municipe',
            'responsavel', 'responsavel_obj', 'responsavel_nome', 'data_criacao',
            'data_atualizacao', 'tramitacoes', 'categorias', 'categorias_ids', 'anexos'
        ]
        read_only_fields = ('protocolo', 'data_criacao', 'data_atualizacao')

    def get_responsavel_nome(self, obj):
        # Retorna o nome completo se existir, senão o username
        if obj.responsavel:
            return obj.responsavel.get_full_name() or obj.responsavel.username
        return None

    def update(self, instance, validated_data):
        categorias_data = validated_data.pop('categorias', None)
        instance = super().update(instance, validated_data)
        if categorias_data is not None:
            instance.categorias.set(categorias_data)
        return instance

class SolicitacaoAgendaSerializer(serializers.ModelSerializer):
    solicitante_nome = serializers.CharField(source='solicitante.nome_completo', read_only=True)
    conta_nome = serializers.CharField(source='conta.nome', read_only=True)
    # Adicionamos um campo para mostrar os detalhes do espaço na leitura
    espaco_detalhes = EspacoSerializer(source='espaco', read_only=True)

    class Meta:
        model = SolicitacaoAgenda
        fields = '__all__'

    def validate(self, data):
        """
        Validação customizada para verificar conflitos de agendamento.
        """
        # Só precisamos validar se a agenda está sendo confirmada ("AGENDADO")
        # e se todos os dados necessários (espaço, início e fim) foram fornecidos.
        status = data.get('status')
        espaco = data.get('espaco')
        inicio = data.get('data_agendada')
        fim = data.get('data_agendada_fim')

        if status == 'AGENDADO' and espaco and inicio and fim:
            # Garante que a data de término não seja anterior à data de início
            if fim <= inicio:
                raise serializers.ValidationError("O horário de término deve ser posterior ao horário de início.")

            # Busca por agendamentos conflitantes no mesmo espaço e com status 'AGENDADO'
            # A lógica de sobreposição é:
            # (Início da outra < Fim da minha) E (Fim da outra > Início da minha)
            agendas_conflitantes = SolicitacaoAgenda.objects.filter(
                espaco=espaco,
                status='AGENDADO',
                data_agendada__lt=fim,
                data_agendada_fim__gt=inicio
            )

            # Se estivermos atualizando uma agenda existente, devemos excluí-la da verificação
            if self.instance:
                agendas_conflitantes = agendas_conflitantes.exclude(pk=self.instance.pk)

            if agendas_conflitantes.exists():
                raise serializers.ValidationError({
                    'espaco': f"Conflito de agendamento. O espaço '{espaco.nome}' já está reservado neste horário."
                })
            
        return data

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        # Pega o token padrão
        token = super().get_token(user)

        # Adiciona dados customizados do usuário e perfil ao token
        token['username'] = user.username
        token['is_superuser'] = user.is_superuser
        token['groups'] = list(user.groups.values_list('name', flat=True))
        token['user_permissions'] = list(user.get_all_permissions())

        if hasattr(user, 'perfil'):
            perfil_data = {
                "id": user.perfil.id,
                
                # --- A LÓGICA CORRETA E DEFINITIVA ---
                # Pega uma lista de TODOS os IDs de contas associadas ao perfil.
                "contas": list(user.perfil.contas.all().values_list('id', flat=True))
            }
            token['perfil'] = perfil_data
        
        return token
    
class CategoriaContatoSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaContato
        fields = '__all__'
    
class MunicipeDetailSerializer(serializers.ModelSerializer):
    atendimentos = AtendimentoSerializer(many=True, read_only=True)
    solicitacoes_agenda = SolicitacaoAgendaSerializer(many=True, read_only=True)
    contas = ContaSerializer(many=True, read_only=True)
    categoria = CategoriaContatoSerializer(read_only=True)

    class Meta:
        model = Municipe
        fields = [
            'id', 'nome_completo', 'nome_de_guerra', 'cpf', 'data_nascimento', 'emails', 
            'telefones', 'endereco', 'observacoes', 'cargo', 'orgao', 
            'contas', 'categoria', 
            'atendimentos', 'solicitacoes_agenda'
        ]
    
class BuscaGlobalSerializer(serializers.Serializer):
    """
    Um serializer para formatar os resultados da busca global,
    indicando o tipo de cada resultado.
    """
    TIPO_CHOICES = (
        ('atendimento', 'Atendimento'),
        ('municipe', 'Munícipe'),
    )
    tipo = serializers.ChoiceField(choices=TIPO_CHOICES)
    id = serializers.IntegerField()
    texto_principal = serializers.CharField(max_length=255)
    texto_secundario = serializers.CharField(max_length=255)
    url = serializers.CharField(max_length=255)

class NotificacaoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notificacao
        fields = ['id', 'mensagem', 'link', 'lida', 'data_criacao']
        read_only_fields = ['data_criacao']

class MunicipeLookupSerializer(serializers.ModelSerializer):
    pode_editar = serializers.SerializerMethodField()
    qualidade_dados = serializers.SerializerMethodField()
    alerta_atualizacao = serializers.SerializerMethodField()
    contas = ContaSerializer(many=True, read_only=True)

    class Meta:
        model = Municipe
        fields = ['id', 'nome_completo', 'nome_de_guerra', 'contas', 'categoria', 'cargo', 'emails', 'pode_editar', 'qualidade_dados', 'alerta_atualizacao']

    def get_pode_editar(self, obj):
        user = self.context['request'].user

        if user.is_superuser:
            return True

        if is_in_group(user, 'Recepção'):
            # Regra 1: O contato DEVE ser da categoria 'Munícipe'.
            if not (obj.categoria is not None and obj.categoria.nome == 'MUNÍCIPE'):
                return False
            
            # Regra 2: Pode editar se o munícipe for público (sem conta vinculada).
            if not obj.contas.exists():
                return True
            
            # Regra 3: Se tiver conta, pode editar se houver uma conta em comum.
            if hasattr(user, 'perfil'):
                user_contas = set(user.perfil.contas.all())
                municipe_contas = set(obj.contas.all())
                return not user_contas.isdisjoint(municipe_contas)
            
            return False

        if is_in_group(user, 'Membro do Gabinete') or is_in_group(user, 'Secretária'):
            # Pode editar se for público
            if not obj.contas.exists():
                return True
            
            # Pode editar se houver intersecção entre as contas do usuário e as do munícipe
            if hasattr(user, 'perfil'):
                user_contas = set(user.perfil.contas.all())
                municipe_contas = set(obj.contas.all())
                return not user_contas.isdisjoint(municipe_contas) # Retorna True se houver pelo menos uma conta em comum

        return False
    
    def get_qualidade_dados(self, obj):
        score = 0
        if obj.cpf and obj.cpf.strip(): score += 1
        if obj.emails and obj.emails[0].get('email'): score += 1
        if obj.telefones: score += 1
        if obj.endereco and obj.endereco.get('cep'): score += 1

        if score == 4: return "Completo"
        if score >= 2: return "Parcial"
        return "Baixo"

    def get_alerta_atualizacao(self, obj):
        if not obj.data_atualizacao: return True
        diferenca = timezone.now() - obj.data_atualizacao
        return diferenca.days > 180
    
class EspacoAgendaSerializer(serializers.ModelSerializer):
    """
    Serializer otimizado para alimentar componentes de calendário.
    Transforma uma Solicitação de Agenda em um evento de calendário.
    """
    title = serializers.CharField(source='assunto')
    start = serializers.DateTimeField(source='data_agendada')
    end = serializers.DateTimeField(source='data_agendada_fim')
    
    class Meta:
        model = SolicitacaoAgenda
        fields = ('id', 'title', 'start', 'end')


class RegistroVisitaSerializer(serializers.ModelSerializer):
    municipe_nome = serializers.CharField(source='municipe.nome_completo', read_only=True)
    conta_destino_nome = serializers.CharField(source='conta_destino.nome', read_only=True)
    registrado_por_nome = serializers.CharField(source='registrado_por.username', read_only=True)

    class Meta:
        model = RegistroVisita
        fields = [
            'id', 'municipe', 'municipe_nome', 'conta_destino', 'conta_destino_nome',
            'data_checkin', 'observacao', 'registrado_por', 'registrado_por_nome'
        ]

class ReservaEspacoSerializer(serializers.ModelSerializer):
    espaco_nome = serializers.CharField(source='espaco.nome', read_only=True)
    responsavel_nome = serializers.CharField(source='responsavel.get_full_name', read_only=True)
    solicitante_nome = serializers.CharField(source='solicitante.nome_completo', read_only=True, required=False)

    class Meta:
        model = ReservaEspaco
        fields = '__all__'
        read_only_fields = ('responsavel',) # O responsável será o usuário logado

    def validate(self, data):
        inicio = data.get('data_inicio')
        fim = data.get('data_fim')
        espaco = data.get('espaco')

        if fim <= inicio:
            raise serializers.ValidationError("O horário de término deve ser posterior ao de início.")

        # Verifica conflitos de agendamento
        conflitos = ReservaEspaco.objects.filter(
            espaco=espaco,
            data_inicio__lt=fim,
            data_fim__gt=inicio
        )

        # Se estivermos atualizando, excluímos a própria instância da verificação
        if self.instance:
            conflitos = conflitos.exclude(pk=self.instance.pk)

        if conflitos.exists():
            raise serializers.ValidationError(f"Conflito de agendamento. O espaço '{espaco.nome}' já está reservado neste horário.")
        
        return data

class LembreteSerializer(serializers.ModelSerializer):
    # Campos adicionais para facilitar a exibição no frontend
    usuario_nome = serializers.CharField(source='usuario.get_full_name', read_only=True)
    conta_nome = serializers.CharField(source='conta.nome', read_only=True)

    class Meta:
        model = Lembrete
        fields = [
            'id', 
            'conta', 
            'conta_nome',
            'usuario', 
            'usuario_nome',
            'titulo', 
            'conteudo', 
            'data_criacao', 
            'data_atualizacao'
        ]
        read_only_fields = ['usuario']