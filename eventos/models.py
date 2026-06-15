import uuid
from django.db import models
from django.core.exceptions import ValidationError
from atendimentos.models import Conta, Municipe, PerfilMunicipe

class UppercaseFieldsMixin:
    """
    Mixin para converter automaticamente os campos CharField e TextField para
    letras maiúsculas ao salvar o objeto.
    """
    UPPERCASE_EXCEPTIONS = ('emails', 'email', 'endereco', 'descricao', 'status')

    def save(self, *args, **kwargs):

        for field in self._meta.fields:
            field_value = getattr(self, field.name)
            if isinstance(field, (models.CharField, models.TextField)) and field_value and field.name not in self.UPPERCASE_EXCEPTIONS:
                setattr(self, field.name, field_value.upper())
        super().save(*args, **kwargs)

class Evento(UppercaseFieldsMixin, models.Model):
    STATUS_CHOICES = [
        ('agendado', 'Agendado'),
        ('cancelado', 'Cancelado'),
        ('concluido', 'Concluído'),
        ('standby', 'Stand-by'),
    ]

    conta = models.ForeignKey(
        Conta,
        on_delete=models.PROTECT,
        related_name='eventos',
        help_text="Conta à qual este evento pertence."
    )

    nome = models.CharField(max_length=200)
    descricao = models.TextField(blank=True, null=True)
    data_evento = models.DateTimeField()
    criado_em = models.DateTimeField(auto_now_add=True)
    local = models.CharField(max_length=255, help_text="Endereço ou nome do espaço do evento")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='agendado')
    ativo = models.BooleanField(default=True, help_text="Controla se o evento está ativo para check-in via QR Code.")

    def save(self, *args, **kwargs):
        """
        Sobrescreve o método de salvar com a lógica "inteligente":
        Ao ativar este evento, desativa automaticamente qualquer outro evento ativo da mesma conta.
        """
        if self.ativo:
            Evento.objects.filter(
                conta=self.conta, 
                ativo=True
            ).exclude(pk=self.pk).update(ativo=False)

        super().save(*args, **kwargs)

    class Meta:
        permissions = [
            ("pode_gerenciar_eventos", "Pode gerenciar o módulo de eventos"),
        ]

    def __str__(self):
        return self.nome

class Convidado(models.Model):
    """Convite vinculado ao Perfil (cargo/órgão). Fonte da verdade: perfil; municipe opcional para fallback."""
    STATUS_CHOICES = [
        ('convidado', 'Convidado'),
        ('confirmado', 'Confirmado'),
        ('presente', 'Presente'),
    ]

    evento = models.ForeignKey(
        Evento,
        on_delete=models.CASCADE,
        related_name='convidados'
    )
    perfil = models.ForeignKey(
        PerfilMunicipe,
        on_delete=models.PROTECT,
        related_name='convites',
        verbose_name="Perfil (Cargo/Órgão) no convite"
    )
    municipe = models.ForeignKey(
        Municipe,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='convites_legado',
        verbose_name="Munícipe (fallback)"
    )
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='convidado')
    data_checkin = models.DateTimeField(null=True, blank=True, verbose_name="Data do Check-in")
    ordem = models.PositiveIntegerField(default=0, help_text="Campo para ordenação manual.")

    class Meta:
        unique_together = ('evento', 'perfil')
        verbose_name = "Convidado"
        verbose_name_plural = "Convidados"
        ordering = ['ordem']

    def __str__(self):
        return f"{self.perfil.municipe.nome_completo} no evento {self.evento.nome}"

class ListaPresenca(UppercaseFieldsMixin, models.Model):
    evento = models.ForeignKey(Evento, on_delete=models.CASCADE, related_name='presentes')
    municipe = models.ForeignKey(Municipe, on_delete=models.PROTECT, related_name='presencas')
    nome_completo = models.CharField(max_length=255)
    telefone = models.CharField(max_length=20)
    email = models.EmailField(max_length=255, blank=True, null=True)
    instituicao_orgao = models.CharField(max_length=255, blank=True, null=True, verbose_name="Instituição/Órgão")
    data_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('evento', 'municipe')
        verbose_name = "Lista de Presença"
        verbose_name_plural = "Listas de Presença"

    def __str__(self):
        return f"Presença de {self.nome_completo} no evento {self.evento.nome}"

class ChecklistItem(UppercaseFieldsMixin, models.Model):
    nome = models.CharField(max_length=255, help_text="Nome do serviço ou material a ser verificado.")
    
    class Meta:
        verbose_name = "Item Mestre de Checklist"
        verbose_name_plural = "Itens Mestres de Checklist"
        ordering = ['nome']

    def __str__(self):
        return self.nome


class EventoChecklist(UppercaseFieldsMixin, models.Model):
    evento = models.OneToOneField(Evento, on_delete=models.CASCADE, related_name='checklist')
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    
    nome_responsavel = models.CharField(max_length=255, blank=True, null=True, verbose_name="Nome do Responsável que preencheu")
    token_usado = models.BooleanField(default=False, verbose_name="Link de preenchimento já foi usado?")
    data_envio = models.DateTimeField(null=True, blank=True, verbose_name="Data de Envio do Checklist")

    class Meta:
        verbose_name = "Checklist do Evento"
        verbose_name_plural = "Checklists dos Eventos"

    def __str__(self):
        return f"Checklist para {self.evento.nome}"


class EventoChecklistItemStatus(UppercaseFieldsMixin, models.Model):
    # Observações são texto livre: não forçar maiúsculas (preserva conteúdo legítimo).
    UPPERCASE_EXCEPTIONS = ('emails', 'email', 'endereco', 'descricao', 'status', 'observacoes')

    evento_checklist = models.ForeignKey(EventoChecklist, on_delete=models.CASCADE, related_name='itens_status')
    item_mestre = models.ForeignKey(ChecklistItem, on_delete=models.PROTECT, verbose_name="Item")
    concluido = models.BooleanField(default=False, verbose_name="Concluído")
    observacoes = models.TextField(blank=True, null=True, verbose_name="Observações/Dados")

    class Meta:
        verbose_name = "Status do Item de Checklist"
        verbose_name_plural = "Status dos Itens de Checklist"
        # Garante que um item mestre não se repita no mesmo checklist
        unique_together = ('evento_checklist', 'item_mestre')

    def __str__(self):
        return self.item_mestre.nome

class Comunicacao(models.Model):
    STATUS_CHOICES = [
        ('criado', 'Criado'),
        ('enviado', 'Enviado'),
        ('cancelado', 'Cancelado'),
    ]

    evento = models.ForeignKey(Evento, on_delete=models.CASCADE, related_name='comunicacoes')
    titulo = models.CharField(max_length=255)
    descricao = models.TextField(verbose_name="Descrição (Corpo do E-mail)")
    arte = models.ImageField(upload_to='comunicacoes/artes/', blank=True, null=True, help_text="Arte principal do comunicado (imagem)")
    anexo = models.FileField(upload_to='comunicacoes/anexos/', blank=True, null=True, help_text="Documento anexo (PDF, etc.)")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='criado')
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_envio = models.DateTimeField(null=True, blank=True)
    grupos_inclusao = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Grupos de inclusão (categoria / mailing)",
        help_text="Histórico de inclusões em lote por categoria ou lista de mailing.",
    )

    def __str__(self):
        return f"{self.titulo} (Evento: {self.evento.nome})"

    class Meta:
        ordering = ['-data_criacao']
        verbose_name = "Comunicação"
        verbose_name_plural = "Comunicações"

class Destinatario(models.Model):
    """
    Representa um munícipe que é um destinatário para as comunicações de um evento.
    """
    comunicacao = models.ForeignKey(Comunicacao, on_delete=models.CASCADE, related_name='destinatarios')
    municipe = models.ForeignKey(Municipe, on_delete=models.PROTECT, related_name='destinos_comunicacao')

    class Meta:
        unique_together = ('comunicacao', 'municipe')
        verbose_name = "Destinatário"
        verbose_name_plural = "Destinatários"

    def __str__(self):
        return f"{self.municipe.nome_completo} - Destinatário da comunicação: {self.comunicacao.titulo}"

class LogDeEnvio(models.Model):
    STATUS_CHOICES = [
        ('sucesso', 'Sucesso'),
        ('falha', 'Falha'),
    ]
    comunicacao = models.ForeignKey(Comunicacao, on_delete=models.CASCADE, related_name='logs')
    destinatario = models.ForeignKey(Destinatario, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    data_envio = models.DateTimeField(auto_now_add=True)
    detalhe_erro = models.TextField(blank=True, null=True, verbose_name="Detalhe do Erro")

    def __str__(self):
        return f"Log para {self.comunicacao.titulo} -> {self.destinatario.municipe.nome_completo}"

class MailingList(UppercaseFieldsMixin, models.Model):
    conta = models.ForeignKey(
        Conta,
        on_delete=models.PROTECT,
        related_name='mailing_lists',
        help_text="Conta à qual esta lista de mailing pertence."
    )
    nome = models.CharField(max_length=200, help_text="Nome da lista de mailing (ex: Imprensa, Vereadores).")
    municipes = models.ManyToManyField(
        Municipe,
        related_name='mailing_lists',
        blank=True,
        help_text="Contatos incluídos nesta lista."
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Garante que o nome da lista seja único por conta
        unique_together = ('conta', 'nome')
        ordering = ['nome']
        verbose_name = "Lista de Mailing"
        verbose_name_plural = "Listas de Mailing"

    def __str__(self):
        return self.nome


class EmailSupressao(models.Model):
    """
    Registra e-mails que devem ser bloqueados no envio de comunicações.
    Permite saneamento do banco sem exclusão de dados.
    """
    STATUS_CHOICES = [
        ('ativo', 'Ativo (Bloqueado)'),
        ('liberado', 'Liberado'),
    ]
    MOTIVO_CHOICES = [
        ('bounce', 'Bounce (endereço inválido ou inexistente)'),
        ('invalid_syntax', 'Sintaxe inválida'),
        ('manual', 'Decisão manual'),
        ('outro', 'Outro'),
    ]
    ORIGEM_CHOICES = [
        ('log_envio', 'Log de Envio (automático)'),
        ('usuario', 'Usuário (manual)'),
        ('import', 'Importação'),
    ]

    email = models.EmailField(
        unique=True,
        db_index=True,
        help_text="E-mail normalizado (lowercase) que deve ser suprimido."
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='ativo',
        db_index=True,
        help_text="Status da supressão."
    )
    motivo = models.CharField(
        max_length=20,
        choices=MOTIVO_CHOICES,
        default='bounce',
        help_text="Motivo da supressão."
    )
    origem = models.CharField(
        max_length=15,
        choices=ORIGEM_CHOICES,
        default='log_envio',
        help_text="Origem da supressão."
    )
    primeira_ocorrencia = models.DateTimeField(
        auto_now_add=True,
        help_text="Data da primeira ocorrência/registro."
    )
    ultima_ocorrencia = models.DateTimeField(
        auto_now=True,
        help_text="Data da última ocorrência/atualização."
    )
    ocorrencias = models.PositiveIntegerField(
        default=1,
        help_text="Quantidade de ocorrências de erro para este e-mail."
    )
    conta = models.ForeignKey(
        Conta,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='emails_suprimidos',
        help_text="Conta relacionada (opcional). Se nulo, supressão é global."
    )
    observacao = models.TextField(
        blank=True,
        default='',
        help_text="Observações adicionais sobre a supressão."
    )
    criado_por = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='supressoes_criadas',
        help_text="Usuário que criou o registro."
    )
    atualizado_por = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='supressoes_atualizadas',
        help_text="Usuário que atualizou o registro."
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Supressão de E-mail"
        verbose_name_plural = "Supressões de E-mails"
        ordering = ['-ultima_ocorrencia']
        indexes = [
            models.Index(fields=['email', 'status']),
            models.Index(fields=['status', 'motivo']),
        ]

    def __str__(self):
        return f"{self.email} ({self.get_status_display()})"

    def incrementar_ocorrencia(self, user=None):
        """Incrementa contador de ocorrências e atualiza última ocorrência."""
        self.ocorrencias += 1
        if user:
            self.atualizado_por = user
        self.save(update_fields=['ocorrencias', 'ultima_ocorrencia', 'atualizado_por', 'atualizado_em'])
    
    def get_municipes_relacionados(self):
        """
        Retorna queryset de Municipes que possuem este e-mail.
        Busca em todos os e-mails armazenados no JSONField.
        """
        from django.db.models import Q
        email_lower = self.email.lower()
        
        # Busca em municipes onde o JSONField emails contém este e-mail
        municipes = Municipe.objects.filter(
            Q(emails__icontains=email_lower)
        ).distinct()
        
        return municipes