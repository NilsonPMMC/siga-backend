# Em oficios/models.py
from django.db import models, transaction
from django.contrib.auth.models import User
from atendimentos.models import Conta
from django.utils import timezone

class Oficio(models.Model):
    conta = models.ForeignKey(Conta, on_delete=models.PROTECT, related_name='oficios', verbose_name="Secretaria/Gabinete")
    numero = models.CharField(max_length=50, unique=True, blank=True, editable=False, verbose_name="Número do Ofício")
    ano = models.PositiveIntegerField(editable=False, verbose_name="Ano")
    assunto = models.CharField(max_length=255, verbose_name="Assunto")
    data_documento = models.DateField(default=timezone.now, verbose_name="Data do Documento")
    
    destinatario_tratamento = models.CharField(
        max_length=100, 
        verbose_name="Forma de Tratamento", 
        blank=True, 
        null=True,
        help_text="Ex: Prezado Senhor, Vossa Excelência, A quem possa interessar"
    )
    destinatario_nome = models.CharField(max_length=255, verbose_name="Nome do Destinatário")
    destinatario_cargo = models.CharField(max_length=255, verbose_name="Cargo do Destinatário")
    destinatario_orgao = models.CharField(max_length=255, verbose_name="Órgão/Empresa do Destinatário")
    
    corpo = models.TextField(verbose_name="Corpo do Ofício")
    
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='oficios_criados')
    data_criacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ofício"
        verbose_name_plural = "Ofícios"
        ordering = ['-ano', '-id']
        permissions = [
            ("pode_gerenciar_oficios", "Pode gerenciar o módulo de Ofícios"),
        ]

    def __str__(self):
        return self.numero

    def save(self, *args, **kwargs):
        # Executa a lógica de numeração apenas na criação do objeto
        if not self.pk:
            # Usamos 'transaction.atomic' para garantir que a leitura e atualização do contador
            # sejam uma operação única, evitando duplicidade em caso de múltiplos acessos simultâneos.
            with transaction.atomic():
                # Bloqueia a linha da tabela 'Conta' para escrita, garantindo que nenhum
                # outro processo possa alterar o contador ao mesmo tempo.
                conta_a_atualizar = Conta.objects.select_for_update().get(pk=self.conta.pk)
                
                ano_atual = self.data_documento.year
                
                # Se o ano do documento for diferente do ano de controle da conta, zera o contador.
                if conta_a_atualizar.ano_corrente_oficio != ano_atual:
                    conta_a_atualizar.ano_corrente_oficio = ano_atual
                    conta_a_atualizar.ultimo_numero_oficio = 0

                # Incrementa o contador da conta
                novo_numero = conta_a_atualizar.ultimo_numero_oficio + 1
                conta_a_atualizar.ultimo_numero_oficio = novo_numero
                conta_a_atualizar.save()

                # Atribui os valores ao novo ofício
                self.ano = ano_atual
                sigla = conta_a_atualizar.nome_sigla or "S-S" # Fallback caso a sigla esteja vazia
                self.numero = f"{novo_numero:03d}/{self.ano} - {sigla.upper()}"

        super().save(*args, **kwargs)