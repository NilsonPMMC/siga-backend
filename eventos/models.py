import uuid
from django.db import models
from django.core.exceptions import ValidationError
from atendimentos.models import Conta, Municipe

class Evento(models.Model):
    # Definindo as opções para o campo de status
    STATUS_CHOICES = [
        ('agendado', 'Agendado'),
        ('cancelado', 'Cancelado'),
        ('concluido', 'Concluído'),
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
        # 1. A lógica só é acionada se este evento estiver sendo marcado como ATIVO.
        if self.ativo:
            # 2. Encontra todos os outros eventos da MESMA conta que estão ativos
            #    e, em uma única e eficiente query no banco de dados, os desativa.
            #    O .exclude(pk=self.pk) garante que não estamos desativando o evento
            #    que estamos prestes a salvar como ativo.
            Evento.objects.filter(
                conta=self.conta, 
                ativo=True
            ).exclude(pk=self.pk).update(ativo=False)

        # 3. Após garantir que nenhum outro evento está ativo,
        #    salva o estado atual deste evento (seja ele ativo ou inativo).
        super().save(*args, **kwargs)

    class Meta:
        permissions = [
            ("pode_gerenciar_eventos", "Pode gerenciar o módulo de eventos"),
        ]

    def __str__(self):
        return self.nome

class Convidado(models.Model):
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
    municipe = models.ForeignKey(
        Municipe,
        on_delete=models.PROTECT,
        related_name='convites'
    )
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='convidado')
    data_checkin = models.DateTimeField(null=True, blank=True, verbose_name="Data do Check-in")

    class Meta:
        # Garante que um munícipe só pode ser convidado uma vez para o mesmo evento
        unique_together = ('evento', 'municipe')
        verbose_name = "Convidado"
        verbose_name_plural = "Convidados"

    def __str__(self):
        return f"{self.municipe.nome_completo} no evento {self.evento.nome}"

class ListaPresenca(models.Model):
    evento = models.ForeignKey(Evento, on_delete=models.CASCADE, related_name='presentes')
    municipe = models.ForeignKey(Municipe, on_delete=models.PROTECT, related_name='presencas')
    nome_completo = models.CharField(max_length=255)
    telefone = models.CharField(max_length=20)
    email = models.EmailField(max_length=255, blank=True, null=True)
    instituicao_orgao = models.CharField(max_length=255, blank=True, null=True, verbose_name="Instituição/Órgão")
    data_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Garante que uma pessoa só pode se registrar uma vez no mesmo evento
        unique_together = ('evento', 'municipe')
        verbose_name = "Lista de Presença"
        verbose_name_plural = "Listas de Presença"

    def __str__(self):
        return f"Presença de {self.nome_completo} no evento {self.evento.nome}"

class ChecklistItem(models.Model):
    nome = models.CharField(max_length=255, help_text="Nome do serviço ou material a ser verificado.")
    
    class Meta:
        verbose_name = "Item Mestre de Checklist"
        verbose_name_plural = "Itens Mestres de Checklist"
        ordering = ['nome']

    def __str__(self):
        return self.nome


class EventoChecklist(models.Model):
    evento = models.OneToOneField(Evento, on_delete=models.CASCADE, related_name='checklist')
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    
    # Campos que serão preenchidos pelo responsável via formulário público
    nome_responsavel = models.CharField(max_length=255, blank=True, null=True, verbose_name="Nome do Responsável que preencheu")
    token_usado = models.BooleanField(default=False, verbose_name="Link de preenchimento já foi usado?")
    data_envio = models.DateTimeField(null=True, blank=True, verbose_name="Data de Envio do Checklist")

    class Meta:
        verbose_name = "Checklist do Evento"
        verbose_name_plural = "Checklists dos Eventos"

    def __str__(self):
        return f"Checklist para {self.evento.nome}"


class EventoChecklistItemStatus(models.Model):
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
        # Garante que um munícipe só pode ser adicionado uma vez à lista de destinatários do mesmo evento
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