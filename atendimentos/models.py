import uuid
from django.db import models
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

class UppercaseFieldsMixin:
    UPPERCASE_EXCEPTIONS = ('emails', 'endereco')

    def save(self, *args, **kwargs):
        for field in self._meta.fields:
            if field.name not in self.UPPERCASE_EXCEPTIONS:
                if isinstance(field, (models.CharField, models.TextField)) and getattr(self, field.name):
                    setattr(self, field.name, getattr(self, field.name).upper())
        super().save(*args, **kwargs)

class Conta(UppercaseFieldsMixin, models.Model):
    nome_instituicao = models.CharField(
        max_length=255,
        blank=True, null=True,
        verbose_name="Nome da Instituição (Ex: Prefeitura de Mogi das Cruzes)"
    )
    nome = models.CharField(max_length=100, unique=True, verbose_name="Nome da Conta/Gabinete")
    brasao_instituicao = models.ImageField(
        upload_to='logos/',
        blank=True, null=True,
        verbose_name="Brasão da Instituição (para relatórios)"
    )
    logo_conta = models.ImageField(
        upload_to='logos/',
        blank=True, null=True,
        verbose_name="Logo da Conta/Secretaria (opcional)"
    )
    google_calendar_id = models.EmailField(
        max_length=255,
        blank=True, null=True, # Permite que o campo fique vazio
        verbose_name="ID (email) do Google Calendar para Visualização"
    )
    class Meta: verbose_name = "Conta"; verbose_name_plural = "Contas"
    def __str__(self): return self.nome

class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    contas = models.ManyToManyField(Conta, blank=True)
    pode_visualizar_agendas_compartilhadas = models.BooleanField(
        default=False,
        verbose_name="Pode visualizar agendas compartilhadas?"
    )
    def __str__(self): return f"Perfil de {self.usuario.username}"

class CategoriaContato(UppercaseFieldsMixin, models.Model):
    nome = models.CharField(max_length=100, unique=True)
    ativa = models.BooleanField(default=True)

    def __str__(self):
        return self.nome

    class Meta:
        ordering = ['nome']

class Municipe(UppercaseFieldsMixin, models.Model):
    nome_completo = models.CharField(max_length=255, verbose_name="Nome Completo")
    tratamento = models.CharField(
        max_length=50, 
        blank=True, 
        null=True, 
        verbose_name="Pronome de Tratamento",
        help_text="Ex: Senhor, Senhora, Dr., Dra., Vossa Excelência"
    )
    nome_de_guerra = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        verbose_name="Nome de Guerra / Apelido"
    )
    categoria = models.ForeignKey(
        CategoriaContato, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Categoria do Contato"
    )
    contas = models.ManyToManyField(
        Conta,
        blank=True,
        related_name='municipes',
        verbose_name="Contas com Acesso"
    )
    cpf = models.CharField(max_length=14, unique=True, blank=True, null=True, default=None, verbose_name="CPF")
    data_nascimento = models.DateField(blank=True, null=True, verbose_name="Data de Nascimento")
    emails = models.JSONField(default=list, blank=True, null=True, verbose_name="Emails")
    cargo = models.CharField(max_length=150, blank=True, null=True, verbose_name="Cargo")
    orgao = models.CharField(max_length=150, blank=True, null=True, verbose_name="Órgão/Empresa")
    telefones = models.JSONField(default=list, blank=True, null=True, verbose_name="Telefones")
    endereco = models.JSONField(default=dict, blank=True, null=True, verbose_name="Endereço")
    observacoes = models.TextField(blank=True, null=True, verbose_name="Observações")
    data_cadastro = models.DateTimeField(auto_now_add=True, verbose_name="Data de Cadastro")
    data_atualizacao = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")
    matricula_rh = models.CharField(max_length=50, unique=True, null=True, blank=True, verbose_name="Matrícula RH")
    ativo = models.BooleanField(default=True)
    grupo_duplicado = models.UUIDField(
        null=True, 
        blank=True, 
        db_index=True, # Otimiza a busca por este campo
        verbose_name="Grupo de Possíveis Duplicatas"
    )
    class Meta: verbose_name = "Munícipe"; verbose_name_plural = "Munícipes"; ordering = ['nome_completo']
    def __str__(self): return self.nome_completo

class CategoriaAtendimento(UppercaseFieldsMixin, models.Model):
    nome = models.CharField(max_length=100, unique=True, verbose_name="Nome da Categoria")
    descricao = models.TextField(blank=True, null=True, verbose_name="Descrição")
    ativa = models.BooleanField(default=True, verbose_name="Está ativa?")
    class Meta: verbose_name = "Categoria de Atendimento"; verbose_name_plural = "Categorias de Atendimento"; ordering = ['nome']
    def __str__(self): return self.nome

class Atendimento(models.Model):
    STATUS_CHOICES = [('ABERTO', 'Aberto'), ('EM_ANALISE', 'Em Análise'), ('ENCAMINHADO', 'Encaminhado'), ('CONCLUIDO', 'Concluído'), ('ARQUIVADO', 'Arquivado')]
    protocolo = models.CharField(max_length=20, unique=True, blank=True, editable=False, verbose_name="Protocolo")
    titulo = models.CharField(max_length=255, verbose_name="Título do Atendimento")
    descricao = models.TextField(verbose_name="Descrição Detalhada")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ABERTO', verbose_name="Status")
    categorias = models.ManyToManyField(CategoriaAtendimento, blank=True, related_name="atendimentos", verbose_name="Categorias")
    conta = models.ForeignKey(Conta, on_delete=models.PROTECT, related_name='atendimentos', verbose_name="Conta/Gabinete")
    municipe = models.ForeignKey(Municipe, on_delete=models.PROTECT, related_name='atendimentos', verbose_name="Munícipe")
    responsavel = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='atendimentos_responsaveis', verbose_name="Responsável")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='atendimentos_criados')
    data_criacao = models.DateTimeField(auto_now_add=True, verbose_name="Data de Criação")
    data_atualizacao = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")
    class Meta: verbose_name = "Atendimento"; verbose_name_plural = "Atendimentos"; ordering = ['-data_criacao']
    def __str__(self): return f"{self.protocolo} - {self.titulo}"
    def save(self, *args, **kwargs):
        if not self.protocolo:
            current_year = timezone.now().year
            last_atendimento = Atendimento.objects.filter(protocolo__startswith=f'{current_year}-').order_by('protocolo').last()
            new_number = int(last_atendimento.protocolo.split('-')[1]) + 1 if last_atendimento else 1
            self.protocolo = f'{current_year}-{new_number:05d}'
        
        self.titulo = self.titulo.upper() if self.titulo else ''
        self.descricao = self.descricao.upper() if self.descricao else ''
        super().save(*args, **kwargs)

class Tramitacao(UppercaseFieldsMixin, models.Model):
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, related_name='tramitacoes', verbose_name="Atendimento")
    despacho = models.TextField(verbose_name="Despacho / Nota de Progresso")
    usuario = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="Usuário Responsável")
    data_tramitacao = models.DateTimeField(auto_now_add=True, verbose_name="Data")
    class Meta: verbose_name = "Tramitação"; verbose_name_plural = "Tramitações"; ordering = ['-data_tramitacao']
    def __str__(self): return f"Tramitação em {self.data_tramitacao.strftime('%d/%m/%Y %H:%M')} por {self.usuario.username}"

class Anexo(models.Model):
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, related_name='anexos', verbose_name="Atendimento")
    arquivo = models.FileField(upload_to='anexos/%Y/%m/%d/', verbose_name="Arquivo")
    descricao = models.CharField(max_length=255, blank=True, null=True, verbose_name="Descrição do Arquivo")
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Enviado por")
    data_upload = models.DateTimeField(auto_now_add=True, verbose_name="Data de Upload")
    class Meta: verbose_name = "Anexo"; verbose_name_plural = "Anexos"; ordering = ['-data_upload']
    def __str__(self): return self.arquivo.name.split('/')[-1]

class Espaco(UppercaseFieldsMixin, models.Model):
    nome = models.CharField(max_length=100, unique=True, verbose_name="Nome do Espaço")
    capacidade = models.PositiveIntegerField(default=0, verbose_name="Capacidade de Pessoas")
    descricao = models.TextField(blank=True, null=True, verbose_name="Descrição e Recursos (Ex: Possui projetor)")
    ativo = models.BooleanField(default=True)
    contas = models.ManyToManyField(
        Conta,
        related_name="espacos",
        verbose_name="Contas com Acesso"
    )

    class Meta:
        verbose_name = "Espaço"
        verbose_name_plural = "Espaços"
        ordering = ['nome']

    def __str__(self):
        return self.nome

class SolicitacaoAgenda(UppercaseFieldsMixin, models.Model):
    STATUS_AGENDA_CHOICES = [('SOLICITADO', 'Solicitado'), ('EM_ANALISE', 'Em Análise'), ('AGENDADO', 'Agendado'), ('NEGADO', 'Negado'), ('CANCELADO', 'Cancelado')]
    solicitante = models.ForeignKey(Municipe, on_delete=models.PROTECT, related_name='solicitacoes_agenda', verbose_name="Solicitante")
    conta = models.ForeignKey(Conta, on_delete=models.PROTECT, verbose_name="Conta/Gabinete Solicitado")
    assunto = models.CharField(max_length=255, verbose_name="Assunto da Reunião")
    detalhes = models.TextField(blank=True, null=True, verbose_name="Detalhes Adicionais")
    status = models.CharField(max_length=20, choices=STATUS_AGENDA_CHOICES, default='SOLICITADO', verbose_name="Status")
    data_sugerida = models.DateTimeField(blank=True, null=True, verbose_name="Data Sugerida pelo Solicitante")
    data_agendada = models.DateTimeField(blank=True, null=True, verbose_name="Data e Hora de Início")
    
    # --- NOVOS CAMPOS ADICIONADOS ---
    data_agendada_fim = models.DateTimeField(blank=True, null=True, verbose_name="Data e Hora de Término")
    
    # A CORREÇÃO ESTÁ AQUI: Usamos 'Espaco' como uma string
    espaco = models.ForeignKey(
        'Espaco', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='agendas',
        verbose_name="Espaço Reservado"
    )
    # --- FIM DOS NOVOS CAMPOS ---

    link_google_agenda = models.URLField(
        max_length=1024, 
        blank=True, 
        null=True, 
        verbose_name="Link do Evento no Google Agenda"
    )

    responsavel_analise = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Responsável pela Análise")
    motivo_negacao = models.TextField(blank=True, null=True, verbose_name="Motivo da Negação/Cancelamento")
    data_criacao = models.DateTimeField(auto_now_add=True, verbose_name="Data da Solicitação")
    data_atualizacao = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")
    
    class Meta: 
        verbose_name = "Solicitação de Agenda"
        verbose_name_plural = "Solicitações de Agenda"
        ordering = ['-data_criacao']
        
    def __str__(self): 
        return f"Agenda para {self.solicitante.nome_completo} sobre '{self.assunto}'"


class LogDeAtividade(models.Model):
    ACAO_CHOICES = [('CRIACAO', 'Criação de Atendimento'), ('EDICAO', 'Edição de Atendimento'), ('DELECAO', 'Deleção de Atendimento'), ('TRAMITACAO', 'Nova Tramitação'), ('EDICAO_TRAMITACAO', 'Edição de Tramitação'), ('DELECAO_TRAMITACAO', 'Deleção de Tramitação')]
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Usuário")
    acao = models.CharField(max_length=20, choices=ACAO_CHOICES, verbose_name="Ação Realizada")
    detalhes = models.TextField(verbose_name="Detalhes do Log")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Data e Hora")
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    class Meta: verbose_name = "Log de Atividade"; verbose_name_plural = "Logs de Atividades"; ordering = ['-timestamp']
    def __str__(self): return f"{self.usuario.username} - {self.get_acao_display()} em {self.timestamp.strftime('%d/%m/%Y %H:%M')}"

class Notificacao(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notificacoes')
    mensagem = models.CharField(max_length=255)
    link = models.CharField(max_length=255, blank=True, null=True)
    lida = models.BooleanField(default=False)
    data_criacao = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notificação para {self.usuario.username}: {self.mensagem}"

    class Meta:
        ordering = ['-data_criacao']

# Adicione esta nova classe ao final de models.py
class GoogleApiToken(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='google_token')
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    expires_at = models.DateTimeField()

    def __str__(self):
        return f"Token do Google para {self.usuario.username}"


class RegistroVisita(UppercaseFieldsMixin, models.Model):
    """
    Modelo para registrar um check-in/visita rápida, 
    sem a complexidade de um Atendimento.
    """
    municipe = models.ForeignKey(Municipe, on_delete=models.CASCADE, related_name="visitas")
    conta_destino = models.ForeignKey(Conta, on_delete=models.PROTECT, verbose_name="Gabinete de Destino")
    data_checkin = models.DateTimeField(auto_now_add=True, verbose_name="Data do Check-in")
    observacao = models.TextField(blank=True, null=True, verbose_name="Observação")
    registrado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="visitas_registradas")

    def __str__(self):
        return f"Visita de {self.municipe.nome_completo} em {self.data_checkin.strftime('%d/%m/%Y %H:%M')}"

    class Meta:
        ordering = ['-data_checkin']
        verbose_name = "Registro de Visita"
        verbose_name_plural = "Registros de Visita"

class ReservaEspaco(UppercaseFieldsMixin, models.Model):
    espaco = models.ForeignKey(Espaco, on_delete=models.PROTECT, related_name="reservas")
    titulo = models.CharField(max_length=255, verbose_name="Título/Assunto da Reserva")
    solicitante = models.ForeignKey(
        Municipe, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name="reservas_solicitadas"
    )
    data_inicio = models.DateTimeField(verbose_name="Início da Reserva")
    data_fim = models.DateTimeField(verbose_name="Fim da Reserva")
    responsavel = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="reservas_feitas")
    observacoes = models.TextField(blank=True, null=True, verbose_name="Observações")
    data_criacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Reserva de Espaço"
        verbose_name_plural = "Reservas de Espaços"
        ordering = ['data_inicio']

    def __str__(self):
        return f"{self.espaco.nome} - {self.titulo} em {self.data_inicio.strftime('%d/%m/%Y %H:%M')}"