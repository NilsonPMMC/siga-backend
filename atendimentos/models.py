import uuid
from django.db import models
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

class UppercaseFieldsMixin:
    UPPERCASE_EXCEPTIONS = ('emails', 'endereco', 'dados_etiqueta', 'etiqueta_remetente')

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
    nome_sigla = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Sigla da Conta/Gabinete",
        null=True   # Permite ser nulo no banco de dados
    )
    nome_titular = models.CharField(
        max_length=100, 
        verbose_name="Nome do Gestor Titular",
        blank=True,
        null=True
    )
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
    ultimo_numero_oficio = models.IntegerField(
        default=0,
        verbose_name="Último Nº de Ofício Usado",
        help_text="Controla a sequência numérica dos ofícios gerados para esta conta."
    )
    ano_corrente_oficio = models.IntegerField(
        default=2025, # Defina o ano corrente
        verbose_name="Ano de Controle dos Ofícios"
    )
    google_calendar_id = models.EmailField(
        max_length=255,
        blank=True, null=True, # Permite que o campo fique vazio
        verbose_name="ID (email) do Google Calendar para Visualização"
    )
    etiqueta_remetente = models.TextField(
        blank=True, null=True,
        verbose_name="Etiqueta do Remetente",
        help_text="Texto completo que aparecerá no canto do envelope. Use Enter para quebrar linhas."
    )
    participa_escala = models.BooleanField(
        "Participa da Escala de Plantão?", 
        default=False, 
        help_text="Se marcado, esta secretaria será cobrada no relatório de pendências."
    )
    assinatura_eletronica = models.ImageField(
        upload_to='assinaturas/',
        blank=True, 
        null=True,
        verbose_name="Assinatura Eletrônica",
        help_text="Imagem da assinatura eletrônica para uso em ofícios (formato PNG/JPG recomendado)"
    )
    usar_assinatura_eletronica = models.BooleanField(
        default=False,
        verbose_name="Usar Assinatura Eletrônica em Ofícios?",
        help_text="Se marcado, a assinatura eletrônica será incluída nos ofícios gerados. Certifique-se de ter feito upload da imagem da assinatura."
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

def caminho_foto_municipe(instance, filename):
    return f'fotos_municipes/{filename}'

class Municipe(UppercaseFieldsMixin, models.Model):
    foto = models.ImageField(
        upload_to=caminho_foto_municipe, 
        blank=True, 
        null=True, 
        verbose_name="Foto de Perfil"
    )
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
        on_delete=models.PROTECT, 
        null=False, 
        blank=False,
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
    dados_etiqueta = models.TextField(
        blank=True, null=True, 
        verbose_name="Texto para Etiqueta",
        help_text="Formato livre para a impressão de etiquetas. Ex: A/C Dr. Fulano de Tal e Família."
    )
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
    auditoria_ia = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        verbose_name="Auditoria de Qualidade (IA)",
        help_text="Dados de auditoria gerados pela IA: nota de qualidade, classificação, sugestões de correção."
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
    resumo_ia = models.TextField(blank=True, null=True, verbose_name="Resumo Gerado por IA", help_text="Resumo automático gerado pelo Gemini AI")
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
    
    # Campos para rastreamento de mudança de status
    status_anterior = models.CharField(
        max_length=20, 
        choices=Atendimento.STATUS_CHOICES, 
        null=True, 
        blank=True,
        verbose_name="Status Anterior"
    )
    status_novo = models.CharField(
        max_length=20, 
        choices=Atendimento.STATUS_CHOICES, 
        null=True, 
        blank=True,
        verbose_name="Status Novo"
    )
    alterou_status = models.BooleanField(
        default=False, 
        verbose_name="Esta tramitação alterou o status?"
    )
    
    # Campos para encaminhamento (quando status = ENCAMINHADO)
    encaminhado_para_sinapse_id = models.IntegerField(
        null=True, 
        blank=True, 
        verbose_name="ID Sinapse (Secretaria/Órgão)"
    )
    encaminhado_para_nome = models.CharField(
        max_length=255, 
        null=True, 
        blank=True, 
        verbose_name="Nome do Destino (Sinapse)"
    )
    encaminhado_para_tipo = models.CharField(
        max_length=50, 
        null=True, 
        blank=True, 
        verbose_name="Tipo (Secretaria/Setor/etc)"
    )
    
    class Meta: 
        verbose_name = "Tramitação"
        verbose_name_plural = "Tramitações"
        ordering = ['-data_tramitacao']
    
    def __str__(self): 
        if self.alterou_status and self.status_novo:
            return f"Tramitação em {self.data_tramitacao.strftime('%d/%m/%Y %H:%M')} por {self.usuario.username} - Status: {self.get_status_novo_display()}"
        return f"Tramitação em {self.data_tramitacao.strftime('%d/%m/%Y %H:%M')} por {self.usuario.username}"

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
    STATUS_AGENDA_CHOICES = [('SOLICITADO', 'Solicitado'), ('EM_ANALISE', 'Em Análise'), ('AGENDADO', 'Agendado'), ('AGENDAR', 'Agendar'), ('NEGADO', 'Negado'), ('CANCELADO', 'Cancelado'), ('REAGENDAR', 'Reagendar'), ('ENCAMINHADO', 'Encaminhado')]
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
    grupo_recorrencia = models.UUIDField(null=True, blank=True, editable=False, help_text="Agrupa eventos recorrentes.")

    class Meta:
        verbose_name = "Reserva de Espaço"
        verbose_name_plural = "Reservas de Espaços"
        ordering = ['data_inicio']

    def __str__(self):
        return f"{self.espaco.nome} - {self.titulo} em {self.data_inicio.strftime('%d/%m/%Y %H:%M')}"

class Lembrete(UppercaseFieldsMixin, models.Model):
    """
    Modelo para registrar lembretes rápidos para gestores de conta.
    Destinado principalmente ao perfil de Secretária.
    """
    UPPERCASE_EXCEPTIONS = ('conteudo',) # Adicionamos 'conteudo' para não ser convertido para maiúsculas

    conta = models.ForeignKey(
        Conta, 
        on_delete=models.PROTECT, 
        related_name="lembretes",
        verbose_name="Conta do Lembrete"
    )
    usuario = models.ForeignKey(
        User, 
        on_delete=models.PROTECT, 
        related_name="lembretes_criados",
        verbose_name="Criado por"
    )
    titulo = models.CharField(max_length=255, verbose_name="Título do Lembrete")
    conteudo = models.TextField(verbose_name="Conteúdo Detalhado")
    data_criacao = models.DateTimeField(auto_now_add=True, verbose_name="Data de Criação")
    data_atualizacao = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")

    def __str__(self):
        return f"Lembrete '{self.titulo}' para {self.conta.nome} em {self.data_criacao.strftime('%d/%m/%Y')}"

    class Meta:
        ordering = ['-data_criacao']
        verbose_name = "Lembrete"
        verbose_name_plural = "Lembretes"

class TramitacaoAgenda(UppercaseFieldsMixin, models.Model):
    """
    Registra o histórico de interações e despachos de uma solicitação de agenda.
    """
    solicitacao = models.ForeignKey(
        SolicitacaoAgenda, 
        on_delete=models.CASCADE, 
        related_name='tramitacoes', 
        verbose_name="Solicitação de Agenda"
    )
    despacho = models.TextField(verbose_name="Despacho / Nota de Progresso")
    usuario = models.ForeignKey(
        User, 
        on_delete=models.PROTECT, 
        verbose_name="Usuário Responsável"
    )
    data_tramitacao = models.DateTimeField(
        auto_now_add=True, 
        verbose_name="Data"
    )

    class Meta:
        verbose_name = "Tramitação de Agenda"
        verbose_name_plural = "Tramitações de Agenda"
        ordering = ['-data_tramitacao']

    def __str__(self):
        return f"Tramitação em {self.data_tramitacao.strftime('%d/%m/%Y %H:%M')} por {self.usuario.username}"
    
class AgendaCompromisso(UppercaseFieldsMixin, models.Model):
    """
    Representa um evento na agenda oficial da autoridade (Gabinete).
    Diferente da 'SolicitacaoAgenda', este é um evento CONFIRMADO e gerido pela assessoria.
    """
    TIPO_CHOICES = [
        ('REUNIAO_INTERNA', 'Reunião Interna'),
        ('ATENDIMENTO_GABINETE', 'Atendimento no Gabinete'),
        ('EVENTO_EXTERNO', 'Evento Externo'),
        ('VISITA_TECNICA', 'Visita Técnica'),
        ('CERIMONIAL', 'Cerimonial / Solenidade'),
        ('ALMOCO_JANTAR', 'Almoço / Jantar Oficial'),
    ]

    SITUACAO_CHOICES = [
        ('AGENDADO', 'Agendado'),
        ('EM_ANDAMENTO', 'Em Andamento'),
        ('CONCLUIDO', 'Concluído'),
        ('CANCELADO', 'Cancelado'),
        ('ADIADO', 'Adiado'),
    ]

    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name='agenda_institucional', verbose_name="Gabinete/Conta")
    titulo = models.CharField("Assunto / Título", max_length=150)
    descricao = models.TextField("Detalhes / Pauta", blank=True, null=True)
    
    data_inicio = models.DateTimeField("Início")
    data_fim = models.DateTimeField("Fim", blank=True, null=True)
    local = models.CharField("Local", max_length=200, default="Gabinete")
    
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, default='ATENDIMENTO_GABINETE')
    situacao = models.CharField(max_length=20, choices=SITUACAO_CHOICES, default='AGENDADO')
    
    # Segurança
    confidencial = models.BooleanField("Agenda Reservada", default=False, help_text="Se marcado, oculta detalhes para usuários básicos, mostrando apenas horário ocupado.")
    
    # Auditoria
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='compromissos_criados')
    data_criacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-data_inicio']
        verbose_name = "Compromisso Institucional"
        verbose_name_plural = "Agenda Institucional"

    def __str__(self):
        return f"{self.data_inicio.strftime('%d/%m %H:%M')} - {self.titulo}"


class AgendaConvidado(models.Model):
    """
    Pessoas esperadas para um compromisso específico.
    Fundamental para a recepção saber quem liberar sem perguntar "quem é você?".
    """
    compromisso = models.ForeignKey(AgendaCompromisso, on_delete=models.CASCADE, related_name='convidados')
    municipe = models.ForeignKey(Municipe, on_delete=models.CASCADE, related_name='agendas_participantes')
    
    observacao = models.CharField("Observação para Recepção", max_length=100, blank=True, null=True, help_text="Ex: Entrar pelos fundos, liberar carro, etc.")
    
    confirmado = models.BooleanField("Presença Confirmada?", default=False)
    
    # Controle de Acesso (Recepção)
    chegou = models.BooleanField("Realizou Check-in", default=False)
    horario_chegada = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Convidado da Agenda"
        verbose_name_plural = "Convidados da Agenda"

    def __str__(self):
        return f"{self.municipe.nome_completo} em {self.compromisso}"
    
class AgendaCompartilhamento(models.Model):
    NIVEL_CHOICES = [
        ('LEITURA', 'Apenas Visualizar'),
        ('ESCRITA', 'Pode Criar/Editar/Excluir'),
    ]

    # A Agenda que está sendo compartilhada (Ex: Gabinete)
    conta_alvo = models.ForeignKey(
        'Conta', 
        on_delete=models.CASCADE, 
        related_name='compartilhamentos_agenda'
    )
    
    # O Usuário que ganha o acesso (Ex: Secretário de Cultura)
    usuario = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='agendas_compartilhadas'
    )
    
    nivel = models.CharField(max_length=20, choices=NIVEL_CHOICES, default='LEITURA')
    data_criacao = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        verbose_name = "Compartilhamento de Agenda"
        verbose_name_plural = "Compartilhamentos de Agenda"
        unique_together = ('conta_alvo', 'usuario') # Impede duplicar a regra

    def __str__(self):
        return f"{self.conta_alvo} -> {self.usuario} ({self.nivel})"

class SinapseSecretaria(models.Model):
    """
    Cache local da estrutura organizacional da API Sinapse.
    Atualizado periodicamente via comando de management.
    """
    sinapse_id = models.IntegerField(unique=True, verbose_name="ID Sinapse")
    nome = models.CharField(max_length=255, verbose_name="Nome da Secretaria/Órgão")
    sigla = models.CharField(max_length=50, blank=True, null=True, verbose_name="Sigla")
    tipo = models.CharField(max_length=50, verbose_name="Tipo (Secretaria, Setor, etc)")
    hierarquia = models.JSONField(
        null=True, 
        blank=True, 
        help_text="Estrutura hierárquica completa da API Sinapse"
    )
    ativo = models.BooleanField(default=True, verbose_name="Está ativo?")
    data_atualizacao = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")
    
    class Meta:
        verbose_name = "Secretaria Sinapse"
        verbose_name_plural = "Secretarias Sinapse"
        ordering = ['nome']
    
    def __str__(self):
        return f"{self.nome} ({self.sigla or 'Sem sigla'})"