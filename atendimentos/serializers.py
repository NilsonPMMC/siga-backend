from .models import *
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth.models import User
from django.db.models import Count
from django.db import transaction
from .permissions import is_in_group, CanEditMunicipeDetails
from datetime import date
from django.utils import timezone

from .services.perfil_municipe import (
    categorias_nomes_de_perfis,
    contas_ids_escopo_usuario,
    extrair_categoria_id,
    extrair_conta_id,
    municipe_tem_perfis_duplicados,
    parse_categoria_ids_from_request,
    perfis_para_exibicao,
    validar_perfis_sem_duplicata,
)
from .services.escopo_operador_crm import (
    categorias_escopo_usuario,
    is_operador_crm,
    usuario_pode_editar_municipe,
)

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


class AssuntoAtendimentoSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssuntoAtendimento
        fields = ['id', 'nome', 'codigo', 'ordem']

class TramitacaoSerializer(serializers.ModelSerializer):
    # Campo para mostrar o nome do usuário em vez do ID
    usuario_nome = serializers.SerializerMethodField()
    status_anterior_display = serializers.CharField(source='get_status_anterior_display', read_only=True)
    status_novo_display = serializers.CharField(source='get_status_novo_display', read_only=True)

    class Meta:
        model = Tramitacao
        fields = [
            'id', 'despacho', 'usuario', 'usuario_nome', 'data_tramitacao',
            'status_anterior', 'status_anterior_display',
            'status_novo', 'status_novo_display',
            'alterou_status',
            'encaminhado_para_sinapse_id', 'encaminhado_para_nome', 'encaminhado_para_tipo'
        ]
        # O campo 'usuario' será preenchido automaticamente pela view
        read_only_fields = ['usuario', 'usuario_nome', 'data_tramitacao']

    def get_usuario_nome(self, obj):
        # Se o usuário tiver nome completo, use-o. Senão, use o username.
        full_name = obj.usuario.get_full_name()
        return full_name if full_name else obj.usuario.username

class PerfilMunicipeSerializer(serializers.ModelSerializer):
    conta_nome = serializers.CharField(source='conta.nome', read_only=True)
    categoria_nome = serializers.CharField(source='categoria.nome', read_only=True)

    class Meta:
        model = PerfilMunicipe
        fields = [
            'id', 'conta', 'conta_nome', 'categoria', 'categoria_nome', 'cargo', 'instituicao', 'departamento', 'tratamento', 'ativo'
        ]
        extra_kwargs = {
            'conta': {'required': True},
            'categoria': {'required': True},
            'id': {'read_only': False, 'required': False},
        }


class MunicipeSerializer(serializers.ModelSerializer):
    pode_editar = serializers.SerializerMethodField()
    contas = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Conta.objects.all(),
        required=False
    )
    perfis = PerfilMunicipeSerializer(many=True, required=False)
    categorias_nomes = serializers.SerializerMethodField()
    qualidade_dados = serializers.SerializerMethodField()
    alerta_atualizacao = serializers.SerializerMethodField()
    tem_perfis_duplicados = serializers.SerializerMethodField()

    class Meta:
        model = Municipe
        fields = [
            'id', 'foto', 'nome_completo', 'tratamento', 'nome_de_guerra', 'cpf', 'data_nascimento', 'emails',
            'telefones', 'endereco', 'observacoes', 'cargo', 'orgao',
            'contas', 'perfis',
            'categorias_nomes', 'data_cadastro', 'data_atualizacao',
            'qualidade_dados', 'alerta_atualizacao', 'tem_perfis_duplicados',
            'pode_editar', 'grupo_duplicado', 'dados_etiqueta'
        ]
        extra_kwargs = {
            'cpf': {'required': False, 'allow_blank': True},
        }

    def get_categorias_nomes(self, obj):
        request = self.context.get('request')
        categoria_ids = parse_categoria_ids_from_request(request) if request else []
        contas_ids = contas_ids_escopo_usuario(request.user) if request else None
        perfis = perfis_para_exibicao(
            obj,
            categoria_ids=categoria_ids or None,
            contas_ids=contas_ids,
        )
        return categorias_nomes_de_perfis(perfis)
    
    def validate_telefones(self, value):
        if not value or not isinstance(value, list) or len(value) == 0:
            raise serializers.ValidationError("É necessário fornecer pelo menos um número de telefone.")

        telefones_normalizados = []
        telefones_vistos = set()
        for item in value:
            # Aceita payload legado em string: ["(11) 99999-0000", ...]
            if isinstance(item, str):
                numero = item.strip()
                if not numero:
                    raise serializers.ValidationError("O campo 'número' do telefone não pode estar vazio.")
                chave = "".join(ch for ch in numero if ch.isdigit()) or numero.lower()
                if chave in telefones_vistos:
                    continue
                telefones_vistos.add(chave)
                telefones_normalizados.append({"tipo": "principal", "numero": numero})
                continue

            if not isinstance(item, dict):
                raise serializers.ValidationError("Formato inválido para telefone. Use texto ou objeto com 'numero'.")

            numero = str(item.get("numero") or "").strip()
            if not numero:
                raise serializers.ValidationError("O campo 'número' do telefone não pode estar vazio.")

            chave = "".join(ch for ch in numero if ch.isdigit()) or numero.lower()
            if chave in telefones_vistos:
                continue
            telefones_vistos.add(chave)

            tipo = str(item.get("tipo") or "principal").strip() or "principal"
            telefones_normalizados.append({"tipo": tipo, "numero": numero})

        return telefones_normalizados

    def validate_emails(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("Formato inválido para e-mails. Envie uma lista.")

        emails_por_chave = {}
        ordem_emails = []

        def _merge_email(existing, novo):
            if not existing:
                return novo
            # Prioriza item marcado como principal.
            if novo.get("principal") and not existing.get("principal"):
                merged = dict(existing)
                merged.update(novo)
                return merged
            # Se ambos têm mesma prioridade, mantém tipo mais informativo.
            if (existing.get("tipo") in {"", "principal"}) and novo.get("tipo") not in {"", "principal"}:
                existing["tipo"] = novo.get("tipo")
            return existing

        for item in value:
            if isinstance(item, str):
                email = item.strip().lower()
                if not email:
                    continue
                try:
                    validate_email(email)
                except DjangoValidationError:
                    raise serializers.ValidationError(f"E-mail inválido: {email}")
                novo = {"email": email, "tipo": "principal", "principal": False}
                if email not in emails_por_chave:
                    ordem_emails.append(email)
                emails_por_chave[email] = _merge_email(emails_por_chave.get(email), novo)
                continue

            if not isinstance(item, dict):
                raise serializers.ValidationError("Formato inválido para e-mail. Use texto ou objeto com 'email'.")

            email = str(item.get("email") or "").strip().lower()
            if not email:
                continue
            try:
                validate_email(email)
            except DjangoValidationError:
                raise serializers.ValidationError(f"E-mail inválido: {email}")

            tipo = str(item.get("tipo") or "principal").strip() or "principal"
            principal = bool(item.get("principal", False))
            novo = {"email": email, "tipo": tipo, "principal": principal}
            if email not in emails_por_chave:
                ordem_emails.append(email)
            emails_por_chave[email] = _merge_email(emails_por_chave.get(email), novo)

        return [emails_por_chave[chave] for chave in ordem_emails]
    
    def _contas_do_usuario(self):
        request = self.context.get('request')
        if not request or request.user.is_superuser:
            return None
        if hasattr(request.user, 'perfil'):
            return set(request.user.perfil.contas.values_list('id', flat=True))
        return set()

    def _categorias_do_usuario(self):
        request = self.context.get('request')
        if not request:
            return None
        return categorias_escopo_usuario(request.user)

    def _validar_escopo_perfis(self, perfis_data):
        contas_usuario = self._contas_do_usuario()
        categorias_usuario = self._categorias_do_usuario()
        for item in perfis_data:
            conta_id = extrair_conta_id(item)
            if contas_usuario is not None and conta_id and conta_id not in contas_usuario:
                raise serializers.ValidationError({
                    'perfis': [f'Sem permissão para gerenciar perfil da conta {conta_id}.']
                })
            if categorias_usuario is not None:
                categoria_id = extrair_categoria_id(item)
                if not categoria_id:
                    raise serializers.ValidationError({
                        'perfis': ['Operador CRM deve informar categoria permitida em cada perfil.']
                    })
                if categoria_id not in categorias_usuario:
                    raise serializers.ValidationError({
                        'perfis': [f'Sem permissão para a categoria {categoria_id}.']
                    })

    def get_tem_perfis_duplicados(self, obj):
        perfis = getattr(obj, 'perfis', None)
        if perfis is None:
            return False
        return municipe_tem_perfis_duplicados(perfis.all())

    def to_representation(self, instance):
        """
        Este método controla como os dados são MOSTRADOS.
        Ele pega a saída padrão e substitui os IDs das contas pelos detalhes completos.
        """
        representation = super().to_representation(instance)
        representation['contas'] = ContaSerializer(instance.contas.all(), many=True).data

        contas_usuario = self._contas_do_usuario()
        request = self.context.get('request')
        categorias_usuario = self._categorias_do_usuario()
        if categorias_usuario is not None:
            categoria_ids = list(categorias_usuario)
        else:
            categoria_ids = parse_categoria_ids_from_request(request) if request else []

        if representation.get('perfis'):
            perfis_filtrados = representation['perfis']
            if contas_usuario is not None:
                perfis_filtrados = [
                    p for p in perfis_filtrados
                    if (p.get('conta') in contas_usuario)
                ]
            if categoria_ids:
                cat_set = set(categoria_ids)
                perfis_filtrados = [
                    p for p in perfis_filtrados
                    if p.get('categoria') in cat_set
                ]
            representation['perfis'] = perfis_filtrados
        return representation

    def validate_perfis(self, value):
        if value is None:
            return value
        self._validar_escopo_perfis(value)
        municipe = self.instance if getattr(self, 'instance', None) else None
        validar_perfis_sem_duplicata(value, municipe=municipe)
        return value

    def get_pode_editar(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        return usuario_pode_editar_municipe(request.user, obj)

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

    @staticmethod
    def _status_rank(status):
        ranking = {
            'convidado': 1,
            'confirmado': 2,
            'presente': 3,
        }
        return ranking.get((status or '').lower(), 0)

    def _transferir_vinculos_perfil(self, perfil_origem, perfil_destino):
        """
        Move convites vinculados ao perfil de origem para o perfil destino.
        Se já existir convite no mesmo evento para o destino, faz merge e remove duplicado.
        """
        from eventos.models import Convidado

        convites_origem = Convidado.objects.filter(perfil=perfil_origem).select_related('evento')
        for convite in convites_origem:
            conflito = Convidado.objects.filter(
                evento=convite.evento,
                perfil=perfil_destino
            ).exclude(pk=convite.pk).first()

            if conflito:
                # Preserva status mais avançado e check-in mais recente.
                status_escolhido = (
                    conflito.status
                    if self._status_rank(conflito.status) >= self._status_rank(convite.status)
                    else convite.status
                )
                data_checkin = conflito.data_checkin or convite.data_checkin
                if conflito.data_checkin and convite.data_checkin:
                    data_checkin = max(conflito.data_checkin, convite.data_checkin)

                conflito.status = status_escolhido
                conflito.data_checkin = data_checkin
                conflito.municipe = conflito.municipe or perfil_destino.municipe
                conflito.ordem = min(conflito.ordem or 0, convite.ordem or 0)
                conflito.save(update_fields=['status', 'data_checkin', 'municipe', 'ordem'])
                convite.delete()
                continue

            convite.perfil = perfil_destino
            convite.municipe = convite.municipe or perfil_destino.municipe
            convite.save(update_fields=['perfil', 'municipe'])

    def create(self, validated_data):
        perfis_data = validated_data.pop('perfis', [])
        validar_perfis_sem_duplicata(perfis_data)
        instance = super().create(validated_data)
        for item in perfis_data:
            item = {k: v for k, v in item.items() if k != 'id'}
            if 'categoria' not in item and 'categoria_id' not in item:
                cat = CategoriaContato.objects.filter(nome='MUNÍCIPE').first() or CategoriaContato.objects.first()
                if cat:
                    item['categoria_id'] = cat.id
            PerfilMunicipe.objects.create(municipe=instance, **item)
        return instance

    def update(self, instance, validated_data):
        perfis_data = validated_data.pop('perfis', None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if perfis_data is not None:
                self._validar_escopo_perfis(perfis_data)
                validar_perfis_sem_duplicata(perfis_data, municipe=instance)

                contas_usuario = self._contas_do_usuario()
                perfis_existentes_ids = set(instance.perfis.values_list('id', flat=True))
                ids_fora_escopo = set()
                if contas_usuario is not None:
                    ids_fora_escopo = set(
                        instance.perfis.exclude(conta_id__in=contas_usuario).values_list('id', flat=True)
                    )
                categorias_usuario = self._categorias_do_usuario()
                if categorias_usuario is not None:
                    ids_fora_escopo |= set(
                        instance.perfis.exclude(categoria_id__in=categorias_usuario).values_list('id', flat=True)
                    )
                ids_mantidos_ou_atualizados = set(ids_fora_escopo)

                # Primeiro atualiza/cria perfis para definir corretamente os remanescentes.
                for item in perfis_data:
                    perfil_id = item.pop('id', None)
                    payload = {k: v for k, v in item.items() if k != 'id'}
                    if perfil_id and instance.perfis.filter(pk=perfil_id).exists():
                        PerfilMunicipe.objects.filter(pk=perfil_id).update(**payload)
                        ids_mantidos_ou_atualizados.add(perfil_id)
                    else:
                        if 'categoria' not in payload and 'categoria_id' not in payload:
                            cat = CategoriaContato.objects.filter(nome='MUNÍCIPE').first() or CategoriaContato.objects.first()
                            if cat:
                                payload['categoria_id'] = cat.id
                        novo_perfil = PerfilMunicipe.objects.create(municipe=instance, **payload)
                        ids_mantidos_ou_atualizados.add(novo_perfil.id)

                ids_para_remover = perfis_existentes_ids - ids_mantidos_ou_atualizados - ids_fora_escopo
                perfis_para_remover = list(instance.perfis.filter(id__in=ids_para_remover).order_by('id'))

                if perfis_para_remover:
                    perfil_destino = (
                        instance.perfis
                        .filter(id__in=ids_mantidos_ou_atualizados)
                        .order_by('-id')
                        .first()
                    )

                    # Se todos os perfis forem removidos e houver vínculos em eventos, bloqueia.
                    if not perfil_destino:
                        tem_convites = any(p.convites.exists() for p in perfis_para_remover)
                        if tem_convites:
                            raise serializers.ValidationError(
                                "Não é possível remover todos os perfis: existem participações em eventos vinculadas."
                            )
                        PerfilMunicipe.objects.filter(id__in=ids_para_remover).delete()
                    else:
                        for perfil in perfis_para_remover:
                            self._transferir_vinculos_perfil(perfil_origem=perfil, perfil_destino=perfil_destino)
                            perfil.delete()
        return instance

class AtendimentoSerializer(serializers.ModelSerializer):
    # Seus campos de leitura, que já estavam corretos
    nome_municipe = serializers.CharField(source='municipe.nome_completo', read_only=True)
    nome_conta = serializers.CharField(source='conta.nome', read_only=True)
    tramitacoes = TramitacaoSerializer(many=True, read_only=True)
    assunto_obj = AssuntoAtendimentoSerializer(source='assunto', read_only=True)
    assunto_nome = serializers.CharField(source='assunto.nome', read_only=True)
    assunto_ia_sugerido_nome = serializers.CharField(
        source='assunto_ia_sugerido.nome', read_only=True, default=None
    )
    anexos = AnexoSerializer(many=True, read_only=True)
    responsavel_obj = UserSerializer(source='responsavel', read_only=True)

    responsavel = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), write_only=True, required=False, allow_null=True
    )

    assunto_id = serializers.PrimaryKeyRelatedField(
        queryset=AssuntoAtendimento.objects.filter(ativo=True),
        source='assunto',
        write_only=True,
        required=False,
        allow_null=True,
    )

    responsavel_nome = serializers.SerializerMethodField()
    responsaveis_compartilhados = serializers.SerializerMethodField()

    origem_display = serializers.CharField(source='get_origem_display', read_only=True)
    sla_status = serializers.SerializerMethodField()
    sla_status_display = serializers.SerializerMethodField()

    class Meta:
        model = Atendimento
        fields = [
            'id', 'protocolo', 'origem', 'origem_display', 'titulo', 'descricao', 'status', 'conta', 'nome_conta',
            'municipe', 'nome_municipe',
            'assunto', 'assunto_id', 'assunto_obj', 'assunto_nome',
            'assunto_ia_sugerido', 'assunto_ia_sugerido_nome', 'assunto_ia_status',
            'responsavel', 'responsavel_obj', 'responsavel_nome', 'responsaveis_compartilhados', 'data_criacao',
            'data_atualizacao', 'tramitacoes', 'anexos',
            'resumo_ia_local', 'auditoria_ia_status',
            'prazo_resposta', 'prazo_conclusao', 'sla_status', 'sla_status_display',
        ]
        read_only_fields = ('protocolo', 'status', 'data_criacao', 'data_atualizacao', 'prazo_resposta', 'prazo_conclusao')

    def get_sla_status(self, obj):
        from .services.sla_atendimento import calcular_sla_status
        return calcular_sla_status(obj)

    def get_sla_status_display(self, obj):
        from .services.sla_atendimento import calcular_sla_status, sla_status_display
        return sla_status_display(calcular_sla_status(obj))

    def get_responsavel_nome(self, obj):
        if obj.responsavel:
            return obj.responsavel.get_full_name() or obj.responsavel.username
        return None

    def get_responsaveis_compartilhados(self, obj):
        compartilhados = getattr(obj, 'responsaveis_compartilhados', None)
        if compartilhados is None:
            return []
        return [
            {'id': u.id, 'username': u.username, 'first_name': u.first_name, 'last_name': u.last_name}
            for u in compartilhados.all()
        ]

    def validate(self, attrs):
        from .validators import validar_assunto_obrigatorio

        if self.instance:
            assunto_final = attrs.get('assunto', self.instance.assunto)
        else:
            assunto_final = attrs.get('assunto')
        validar_assunto_obrigatorio(assunto_final, instance=self.instance)
        return attrs

    def create(self, validated_data):
        validated_data.pop('categorias', None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Remove status se vier no validated_data (não pode ser alterado diretamente)
        validated_data.pop('status', None)

        if 'assunto' in validated_data and instance.assunto_ia_status in ('PENDENTE', 'APLICADO'):
            validated_data['assunto_ia_status'] = 'REVISADO'

        validated_data.pop('categorias', None)
        assunto_alterado = 'assunto' in validated_data
        instance = super().update(instance, validated_data)
        if assunto_alterado:
            from .services.sla_atendimento import garantir_prazos_sla
            garantir_prazos_sla(instance, force=True)
        return instance

class TramitacaoAgendaSerializer(serializers.ModelSerializer):
    """
    Serializer para o histórico (tramitação) de uma solicitação de agenda.
    """
    usuario_nome = serializers.CharField(source='usuario.get_full_name', read_only=True)
    
    class Meta:
        model = TramitacaoAgenda
        fields = [
            'id', 
            'solicitacao', 
            'despacho', 
            'usuario', 
            'usuario_nome', 
            'data_tramitacao'
        ]
        read_only_fields = ('usuario',)

def _formatar_perfis_municipe(perfis):
    """Formata lista de perfis (cargo/instituição) para exibição em uma única string."""
    if not perfis:
        return ''
    partes = []
    for p in perfis:
        cargo = (p.get('cargo') or '').strip()
        inst = (p.get('instituicao') or '').strip()
        if cargo and inst:
            partes.append(f"{cargo} @ {inst}")
        elif cargo:
            partes.append(cargo)
        elif inst:
            partes.append(inst)
    return '; '.join(partes) if partes else ''


class SolicitacaoAgendaSerializer(serializers.ModelSerializer):
    tramitacoes = TramitacaoAgendaSerializer(many=True, read_only=True)
    solicitante_nome = serializers.CharField(source='solicitante.nome_completo', read_only=True)
    solicitante_perfis_resumo = serializers.SerializerMethodField()
    conta_nome = serializers.CharField(source='conta.nome', read_only=True)
    espaco_detalhes = EspacoSerializer(source='espaco', read_only=True)

    class Meta:
        model = SolicitacaoAgenda
        fields = '__all__'

    def get_solicitante_perfis_resumo(self, obj):
        if not obj.solicitante_id:
            return ''
        perfis = obj.solicitante.perfis.filter(ativo=True).values('cargo', 'instituicao')
        return _formatar_perfis_municipe(list(perfis))

    def validate(self, data):
        """
        Validação customizada para verificar conflitos de agendamento.
        """
        status = data.get('status')
        espaco = data.get('espaco')
        inicio = data.get('data_agendada')
        fim = data.get('data_agendada_fim')

        if status == 'AGENDADO' and espaco and inicio and fim:
            if fim <= inicio:
                raise serializers.ValidationError("O horário de término deve ser posterior ao horário de início.")

            agendas_conflitantes = SolicitacaoAgenda.objects.filter(
                espaco=espaco,
                status='AGENDADO',
                data_agendada__lt=fim,
                data_agendada_fim__gt=inicio
            )

            if self.instance:
                agendas_conflitantes = agendas_conflitantes.exclude(pk=self.instance.pk)

            if agendas_conflitantes.exists():
                raise serializers.ValidationError({
                    'espaco': f"Conflito de agendamento. O espaço '{espaco.nome}' já está reservado neste horário."
                })
            
        return data

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    SAFE_PERMISSION_CLAIMS = {
        "eventos.pode_gerenciar_eventos",
        "oficios.pode_gerenciar_oficios",
    }

    @classmethod
    def get_token(cls, user):
        # Pega o token padrão
        token = super().get_token(user)

        # Adiciona dados customizados do usuário e perfil ao token
        token['username'] = user.username
        token['is_superuser'] = user.is_superuser
        token['groups'] = list(user.groups.values_list('name', flat=True))
        token['user_permissions'] = sorted(
            perm for perm in user.get_all_permissions() if perm in cls.SAFE_PERMISSION_CLAIMS
        )

        if hasattr(user, 'perfil'):
            perfil_data = {
                "id": user.perfil.id,
                "contas": list(user.perfil.contas.all().values_list('id', flat=True)),
                "categorias_contato": list(
                    user.perfil.categorias_contato.all().values_list('id', flat=True)
                ),
            }
            token['perfil'] = perfil_data
        
        return token
    
class CategoriaContatoSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaContato
        fields = '__all__'
    
class MunicipeDetailSerializer(serializers.ModelSerializer):
    atendimentos = AtendimentoSerializer(many=True, read_only=True)
    visitas = serializers.SerializerMethodField()
    presencas_agenda_institucional = serializers.SerializerMethodField()
    solicitacoes_agenda = SolicitacaoAgendaSerializer(many=True, read_only=True)
    contas = ContaSerializer(many=True, read_only=True)
    perfis = PerfilMunicipeSerializer(many=True, read_only=True)
    categorias_nomes = serializers.SerializerMethodField()
    historico_eventos = serializers.SerializerMethodField()
    auditoria_ia = serializers.JSONField(read_only=True)

    class Meta:
        model = Municipe
        fields = [
            'id', 'nome_completo', 'foto', 'nome_de_guerra', 'cpf', 'data_nascimento', 'emails',
            'telefones', 'endereco', 'observacoes', 'cargo', 'orgao',
            'contas', 'perfis', 'categorias_nomes',
            'atendimentos', 'visitas', 'presencas_agenda_institucional',
            'solicitacoes_agenda', 'historico_eventos', 'auditoria_ia'
        ]

    def get_visitas(self, obj):
        return RegistroVisitaSerializer(obj.visitas.all().order_by('-data_checkin'), many=True, context=self.context).data

    def get_presencas_agenda_institucional(self, obj):
        from .models import AgendaConvidado
        participacoes = AgendaConvidado.objects.filter(
            municipe=obj
        ).select_related('compromisso', 'compromisso__conta').order_by('-compromisso__data_inicio')
        resultado = []
        for p in participacoes:
            resultado.append({
                'id': p.id,
                'compromisso_id': p.compromisso_id,
                'titulo': p.compromisso.titulo,
                'data_inicio': p.compromisso.data_inicio.isoformat() if p.compromisso.data_inicio else None,
                'conta_nome': p.compromisso.conta.nome,
                'chegou': p.chegou,
                'horario_chegada': p.horario_chegada.isoformat() if p.horario_chegada else None,
                'confirmado': p.confirmado,
            })
        return resultado

    def get_categorias_nomes(self, obj):
        nomes = set()
        request = self.context.get('request')
        cat_escopo = categorias_escopo_usuario(request.user) if request else None
        for p in obj.perfis.all().select_related('categoria'):
            if not p.ativo:
                continue
            if cat_escopo is not None and p.categoria_id not in cat_escopo:
                continue
            if p.categoria:
                nomes.add(p.categoria.nome)
        return sorted(nomes)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if request and is_operador_crm(request.user):
            data['atendimentos'] = []
            data['visitas'] = []
            data['presencas_agenda_institucional'] = []
            data['solicitacoes_agenda'] = []
            data['historico_eventos'] = []
            cat_escopo = categorias_escopo_usuario(request.user)
            if cat_escopo is not None and data.get('perfis'):
                data['perfis'] = [
                    p for p in data['perfis']
                    if p.get('categoria') in cat_escopo
                ]
        return data

    def get_historico_eventos(self, obj):
        from eventos.models import Convidado
        convites = Convidado.objects.filter(perfil__municipe=obj).select_related('evento').order_by('-evento__data_evento')
        resultado = []
        for convite in convites:
            resultado.append({
                'evento_id': convite.evento.id,
                'nome_evento': convite.evento.nome,
                'data_evento': convite.evento.data_evento,
                'status_participacao': convite.status,
                'local': convite.evento.local
            })
        return resultado
    
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
    score_match = serializers.FloatField(required=False, allow_null=True)
    modo_busca = serializers.ChoiceField(
        choices=[('textual', 'Textual'), ('ia', 'IA')],
        required=False,
        allow_null=True,
    )

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
    perfis = PerfilMunicipeSerializer(many=True, read_only=True)
    categorias_nomes = serializers.SerializerMethodField()

    class Meta:
        model = Municipe
        fields = [
            'id', 'nome_completo', 'nome_de_guerra', 'cpf', 'matricula_rh',
            'contas', 'perfis', 'categorias_nomes', 'cargo', 'emails', 'telefones',
            'pode_editar', 'qualidade_dados', 'alerta_atualizacao',
        ]

    def get_categorias_nomes(self, obj):
        nomes = set()
        for p in obj.perfis.all().select_related('categoria'):
            if p.categoria:
                nomes.add(p.categoria.nome)
        return sorted(nomes)

    def get_pode_editar(self, obj):
        user = self.context['request'].user
        if user.is_superuser:
            return True
        if is_in_group(user, ['Recepção', 'Membro do Gabinete', 'Secretária']) and hasattr(user, 'perfil'):
            user_contas = set(user.perfil.contas.all())
            municipe_contas = set(obj.contas.all())
            return not user_contas.isdisjoint(municipe_contas)
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
    usuario_destino_nome = serializers.SerializerMethodField()
    registrado_por_nome = serializers.CharField(source='registrado_por.username', read_only=True)

    class Meta:
        model = RegistroVisita
        fields = [
            'id', 'municipe', 'municipe_nome', 'conta_destino', 'conta_destino_nome',
            'usuario_destino', 'usuario_destino_nome',
            'data_checkin', 'observacao', 'registrado_por', 'registrado_por_nome',
            'atendimento',
        ]
        read_only_fields = ('atendimento',)

    def get_usuario_destino_nome(self, obj):
        if obj.usuario_destino:
            return obj.usuario_destino.get_full_name() or obj.usuario_destino.username
        return None

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

class AgendaConvidadoSerializer(serializers.ModelSerializer):
    # Dados 'flattened' para facilitar a exibição no card da recepção
    nome_municipe = serializers.ReadOnlyField(source='municipe.nome_completo')
    foto_municipe = serializers.ImageField(source='municipe.foto', read_only=True)
    cargo_municipe = serializers.ReadOnlyField(source='municipe.cargo')
    empresa_municipe = serializers.ReadOnlyField(source='municipe.orgao')
    perfis_municipe_resumo = serializers.SerializerMethodField()
    categoria_municipe = serializers.SerializerMethodField()

    class Meta:
        model = AgendaConvidado
        fields = [
            'id', 'municipe', 'nome_municipe', 'foto_municipe', 
            'cargo_municipe', 'empresa_municipe', 'perfis_municipe_resumo', 'categoria_municipe',
            'observacao', 'confirmado', 'chegou', 'horario_chegada'
        ]

    def get_perfis_municipe_resumo(self, obj):
        if not obj.municipe_id:
            return ''
        perfis = obj.municipe.perfis.filter(ativo=True).values('cargo', 'instituicao')
        return _formatar_perfis_municipe(list(perfis))

    def get_categoria_municipe(self, obj):
        if not obj.municipe_id:
            return ''
        conta_compromisso = obj.compromisso.conta_id
        perfil = obj.municipe.perfis.filter(conta_id=conta_compromisso).select_related('categoria').first()
        if perfil and perfil.categoria:
            return perfil.categoria.nome
        perfil = obj.municipe.perfis.select_related('categoria').first()
        return perfil.categoria.nome if perfil and perfil.categoria else ''

class AgendaCompromissoSerializer(serializers.ModelSerializer):
    convidados = AgendaConvidadoSerializer(many=True, read_only=True)
    
    # Campo extra para o frontend saber de quem é a agenda
    nome_conta = serializers.ReadOnlyField(source='conta.nome')
    
    # Helpers visuais
    status_cor = serializers.SerializerMethodField()
    tipo_label = serializers.CharField(source='get_tipo_display', read_only=True)

    class Meta:
        model = AgendaCompromisso
        fields = [
            'id', 'conta', 'nome_conta', # <--- Aqui está a identificação
            'titulo', 'descricao', 
            'data_inicio', 'data_fim', 'local', 
            'tipo', 'tipo_label', 'situacao', 'confidencial', 
            'convidados', 'status_cor', 'criado_por'
        ]

    def get_status_cor(self, obj):
        # Mapeia cores para o PrimeVue (badges)
        cores = {
            'AGENDADO': 'primary',
            'EM_ANDAMENTO': 'warning',
            'CONCLUIDO': 'success',
            'CANCELADO': 'danger',
            'ADIADO': 'info'
        }
        return cores.get(obj.situacao, 'secondary')
    
class AgendaCompartilhamentoSerializer(serializers.ModelSerializer):
    nome_usuario = serializers.CharField(source='usuario.get_full_name', read_only=True)
    username = serializers.CharField(source='usuario.username', read_only=True)

    class Meta:
        model = AgendaCompartilhamento
        fields = ['id', 'conta_alvo', 'usuario', 'nome_usuario', 'username', 'nivel', 'data_criacao']


# ========================================
# SERIALIZERS MÚLTIPLAS CONTAS GOOGLE - FASE 3
# ========================================

def build_token_status_payload(
    token=None,
    *,
    token_proprio=None,
    token_delegado=None,
    permissao=None,
):
    """
    Contrato unificado para o frontend Google Calendar.

    Leitores SIGA (sem OAuth próprio) usam token delegado de quem já conectou a agenda.
  """
    if token is not None and token_proprio is None:
        token_proprio = token

    pode_autorizar = False
    somente_leitura_siga = False
    if permissao:
        pode_autorizar = bool(permissao.pode_criar or permissao.pode_editar)
        somente_leitura_siga = bool(
            permissao.pode_visualizar
            and not permissao.pode_criar
            and not permissao.pode_editar
            and not permissao.pode_excluir
        )

    token_proprio_valido = bool(token_proprio and not token_proprio.is_expired)
    token_delegado_valido = bool(token_delegado and not token_delegado.is_expired)
    usa_delegado = token_delegado_valido and not token_proprio_valido

    token_ref = token_proprio if token_proprio_valido else (
        token_delegado if token_delegado_valido else None
    )

    if not token_ref:
        return {
            'status': 'not_authenticated',
            'has_valid_token': False,
            'expires_soon': False,
            'expires_at': None,
            'last_updated': None,
            'dias_para_expirar': None,
            'precisa_autorizacao': pode_autorizar,
            'pode_autorizar': pode_autorizar,
            'usa_token_delegado': False,
            'somente_leitura_siga': somente_leitura_siga,
        }

    expiring_soon = token_ref.dias_para_expirar <= 7
    last_updated = token_ref.ultima_renovacao or token_ref.data_atualizacao

    if expiring_soon:
        status = 'expiring_soon'
    else:
        status = 'valid'

    return {
        'status': status,
        'has_valid_token': True,
        'expires_soon': expiring_soon,
        'expires_at': token_ref.expires_at.isoformat() if token_ref.expires_at else None,
        'last_updated': last_updated.isoformat() if last_updated else None,
        'dias_para_expirar': token_ref.dias_para_expirar,
        'precisa_autorizacao': pode_autorizar and not token_proprio_valido,
        'pode_autorizar': pode_autorizar,
        'usa_token_delegado': usa_delegado,
        'somente_leitura_siga': somente_leitura_siga,
    }


class ContaGoogleCalendarSerializer(serializers.ModelSerializer):
    """Serializer para contas Google Calendar com informações de permissões do usuário"""
    
    conta_nome = serializers.CharField(source='conta.nome', read_only=True)
    total_usuarios = serializers.SerializerMethodField()
    permissoes_usuario = serializers.SerializerMethodField()
    token_status = serializers.SerializerMethodField()
    
    class Meta:
        model = ContaGoogleCalendar
        fields = [
            'id', 'nome', 'descricao', 'email_google', 'calendar_id',
            'ativa', 'eh_padrao', 'conta', 'conta_nome', 
            'total_usuarios', 'permissoes_usuario', 'token_status',
            'client_id', 'data_criacao'
        ]
        read_only_fields = ['data_criacao', 'data_atualizacao']
    
    def get_total_usuarios(self, obj):
        """Total de usuários com permissão de visualizar"""
        return obj.permissoes_usuarios.filter(pode_visualizar=True).count()
    
    def get_permissoes_usuario(self, obj):
        """Permissões do usuário atual para esta conta Google"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return None
            
        try:
            permissao = UsuarioContaGooglePermissao.objects.get(
                usuario=request.user,
                conta_google=obj
            )
            return {
                'pode_visualizar': permissao.pode_visualizar,
                'pode_criar': permissao.pode_criar,
                'pode_editar': permissao.pode_editar,
                'pode_excluir': permissao.pode_excluir,
                'nivel_acesso': permissao.nivel_acesso
            }
        except UsuarioContaGooglePermissao.DoesNotExist:
            return None
    
    def get_token_status(self, obj):
        """Status do token (próprio ou delegado para leitura via SIGA)."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return build_token_status_payload()

        from .services.google_calendar_compatibility import GoogleCalendarCompatibilityService
        return GoogleCalendarCompatibilityService.obter_status_token_usuario(
            request.user, obj
        )


class ContaGoogleCalendarListSerializer(serializers.ModelSerializer):
    """Serializer simplificado para listagem"""
    
    conta_nome = serializers.CharField(source='conta.nome', read_only=True)
    permissoes_usuario = serializers.SerializerMethodField()
    token_status = serializers.SerializerMethodField()
    
    class Meta:
        model = ContaGoogleCalendar
        fields = [
            'id', 'nome', 'descricao', 'email_google', 
            'ativa', 'eh_padrao', 'conta_nome',
            'permissoes_usuario', 'token_status'
        ]
    
    def get_permissoes_usuario(self, obj):
        """Permissões resumidas do usuário atual"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return None
            
        try:
            permissao = UsuarioContaGooglePermissao.objects.get(
                usuario=request.user,
                conta_google=obj
            )
            return {
                'pode_criar': permissao.pode_criar,
                'nivel_acesso': permissao.nivel_acesso
            }
        except UsuarioContaGooglePermissao.DoesNotExist:
            return None
    
    def get_token_status(self, obj):
        """Status resumido do token (próprio ou delegado)."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return build_token_status_payload()

        from .services.google_calendar_compatibility import GoogleCalendarCompatibilityService
        return GoogleCalendarCompatibilityService.obter_status_token_usuario(
            request.user, obj
        )


class UsuarioContaGooglePermissaoSerializer(serializers.ModelSerializer):
    """Serializer para permissões de usuários"""
    
    usuario_nome = serializers.SerializerMethodField()
    conta_google_nome = serializers.CharField(source='conta_google.nome', read_only=True)
    nivel_acesso = serializers.CharField(read_only=True)
    
    class Meta:
        model = UsuarioContaGooglePermissao
        fields = [
            'id', 'usuario', 'usuario_nome', 'conta_google', 'conta_google_nome',
            'pode_visualizar', 'pode_criar', 'pode_editar', 'pode_excluir',
            'nivel_acesso', 'data_criacao'
        ]
        read_only_fields = ['data_criacao']
    
    def get_usuario_nome(self, obj):
        """Nome completo do usuário ou username"""
        nome_completo = obj.usuario.get_full_name()
        return nome_completo if nome_completo else obj.usuario.username


class TokenGoogleCalendarSerializer(serializers.ModelSerializer):
    """Serializer para tokens OAuth (apenas informações não sensíveis)"""
    
    usuario_nome = serializers.SerializerMethodField()
    conta_google_nome = serializers.CharField(source='conta_google.nome', read_only=True)
    status = serializers.SerializerMethodField()
    dias_para_expirar = serializers.ReadOnlyField()
    
    class Meta:
        model = TokenGoogleCalendar
        fields = [
            'id', 'usuario', 'usuario_nome', 'conta_google', 'conta_google_nome',
            'expires_at', 'status', 'dias_para_expirar', 
            'data_criacao', 'ultima_renovacao'
        ]
        read_only_fields = ['data_criacao', 'data_atualizacao', 'ultima_renovacao']
    
    def get_usuario_nome(self, obj):
        """Nome do usuário"""
        nome_completo = obj.usuario.get_full_name()
        return nome_completo if nome_completo else obj.usuario.username
    
    def get_status(self, obj):
        """Status do token"""
        if obj.is_expired:
            return 'expired'
        elif obj.dias_para_expirar <= 7:
            return 'expiring_soon'
        else:
            return 'valid'


class GoogleAccountStatusSerializer(serializers.Serializer):
    """Serializer para status consolidado de contas Google do usuário."""

    id = serializers.IntegerField()
    nome = serializers.CharField()
    descricao = serializers.CharField(allow_blank=True, required=False)
    email_google = serializers.EmailField()
    eh_padrao = serializers.BooleanField()
    conta_nome = serializers.CharField()
    calendar_id = serializers.CharField(allow_blank=True, required=False)
    client_id = serializers.CharField(allow_blank=True, required=False)
    total_usuarios = serializers.IntegerField(required=False)

    permissoes_usuario = serializers.DictField(required=False, allow_null=True)
    token_status = serializers.DictField()

    # Campos legados (flat) — mantidos para compatibilidade
    pode_visualizar = serializers.BooleanField(required=False)
    pode_criar = serializers.BooleanField(required=False)
    pode_editar = serializers.BooleanField(required=False)
    pode_excluir = serializers.BooleanField(required=False)
    nivel_acesso = serializers.CharField(required=False, allow_blank=True)
    dias_para_expirar = serializers.IntegerField(allow_null=True, required=False)
    precisa_autorizacao = serializers.BooleanField(required=False)
    authorization_url = serializers.CharField(allow_null=True, required=False)


class OAuthInitiationSerializer(serializers.Serializer):
    """Serializer para iniciar processo OAuth"""
    
    conta_google_id = serializers.IntegerField()
    redirect_uri = serializers.URLField(required=False)
    
    def validate_conta_google_id(self, value):
        """Valida se a conta Google existe e usuário tem permissão"""
        try:
            conta_google = ContaGoogleCalendar.objects.get(
                id=value,
                ativa=True
            )
        except ContaGoogleCalendar.DoesNotExist:
            raise serializers.ValidationError("Conta Google não encontrada ou inativa")
        
        # Verifica se usuário tem permissão
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            try:
                UsuarioContaGooglePermissao.objects.get(
                    usuario=request.user,
                    conta_google=conta_google,
                    pode_visualizar=True
                )
            except UsuarioContaGooglePermissao.DoesNotExist:
                raise serializers.ValidationError("Usuário não tem permissão para esta conta Google")
        
        return value


class EventCreationSerializer(serializers.Serializer):
    """Serializer para criação de eventos com seleção de conta Google"""
    
    conta_google_id = serializers.IntegerField()
    titulo = serializers.CharField(max_length=255)
    descricao = serializers.CharField(required=False, allow_blank=True)
    data_inicio = serializers.DateTimeField()
    data_fim = serializers.DateTimeField()
    local = serializers.CharField(required=False, allow_blank=True, max_length=255)
    
    def validate_conta_google_id(self, value):
        """Valida permissão de criar eventos"""
        try:
            conta_google = ContaGoogleCalendar.objects.get(
                id=value,
                ativa=True
            )
        except ContaGoogleCalendar.DoesNotExist:
            raise serializers.ValidationError("Conta Google não encontrada ou inativa")
        
        # Verifica permissão de criação
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            try:
                permissao = UsuarioContaGooglePermissao.objects.get(
                    usuario=request.user,
                    conta_google=conta_google,
                    pode_criar=True
                )
            except UsuarioContaGooglePermissao.DoesNotExist:
                raise serializers.ValidationError("Usuário não tem permissão para criar eventos nesta conta Google")
        
        return value
    
    def validate(self, data):
        """Validações gerais"""
        if data['data_fim'] <= data['data_inicio']:
            raise serializers.ValidationError("Data de fim deve ser posterior à data de início")
        
        return data


class EventUpdateSerializer(EventCreationSerializer):
    """Atualização de evento em conta Google específica."""

    event_id = serializers.CharField(max_length=255)

    def validate_conta_google_id(self, value):
        try:
            conta_google = ContaGoogleCalendar.objects.get(id=value, ativa=True)
        except ContaGoogleCalendar.DoesNotExist:
            raise serializers.ValidationError("Conta Google não encontrada ou inativa")

        request = self.context.get('request')
        if request and request.user.is_authenticated:
            try:
                UsuarioContaGooglePermissao.objects.get(
                    usuario=request.user,
                    conta_google=conta_google,
                    pode_editar=True,
                )
            except UsuarioContaGooglePermissao.DoesNotExist:
                raise serializers.ValidationError(
                    "Usuário não tem permissão para editar eventos nesta conta Google"
                )

        return value


class LogDeAtividadeSerializer(serializers.ModelSerializer):
    acao_display = serializers.CharField(source='get_acao_display', read_only=True)
    usuario_nome = serializers.SerializerMethodField()
    conta_nome = serializers.CharField(source='conta.nome', read_only=True, default=None)
    entidade = serializers.SerializerMethodField()

    class Meta:
        model = LogDeAtividade
        fields = [
            'id', 'timestamp', 'usuario', 'usuario_nome', 'acao', 'acao_display',
            'detalhes', 'conta', 'conta_nome', 'payload', 'object_id', 'entidade',
        ]
        read_only_fields = fields

    def get_usuario_nome(self, obj):
        if obj.usuario:
            return obj.usuario.get_full_name() or obj.usuario.username
        return None

    def get_entidade(self, obj):
        if obj.content_type:
            return obj.content_type.model
        return None