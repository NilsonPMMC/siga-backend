# escalas/models.py
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class EscalaPeriodo(models.Model):
    nome = models.CharField(max_length=100)
    data_inicio = models.DateField()
    data_fim = models.DateField()
    data_limite_preenchimento = models.DateTimeField(null=True, blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-data_inicio']
        verbose_name = "Período de Escala"
        verbose_name_plural = "Escalas - Períodos"

    def __str__(self):
        return f"{self.nome} ({self.data_inicio.strftime('%d/%m')})"

    @property
    def is_aberto(self):
        if not self.ativo: return False
        if not self.data_limite_preenchimento: return True
        return timezone.now() <= self.data_limite_preenchimento


class EscalaRegistro(models.Model):
    periodo = models.ForeignKey(EscalaPeriodo, on_delete=models.CASCADE, related_name='registros')
    
    # Vínculo com a Secretaria
    conta = models.ForeignKey('atendimentos.Conta', on_delete=models.CASCADE, related_name='escalas_registradas')
    
    # Vínculo com o Servidor (Munícipe)
    servidor = models.ForeignKey('atendimentos.Municipe', on_delete=models.PROTECT, related_name='escalas_realizadas')
    
    # Aqui guardamos O NÚMERO EXATO usado neste plantão (Texto simples para facilitar o relatório/painel)
    telefone_plantao = models.CharField("Telefone Ativo no Plantão", max_length=30)
    
    cargo_funcao_plantao = models.CharField("Função no Plantão", max_length=100, blank=True)
    observacao = models.TextField(blank=True, null=True)
    
    registrado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    data_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Registro de Plantonista"
        verbose_name_plural = "Escalas - Registros"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        
        # --- LÓGICA DE ATUALIZAÇÃO DO JSON DE TELEFONES ---
        try:
            municipe = self.servidor
            
            # 1. Normaliza o novo número (remove espaços extras)
            novo_numero = str(self.telefone_plantao).strip()
            
            if novo_numero:
                # 2. Obtém a lista atual ou cria vazia
                telefones_atuais = municipe.telefones or []
                
                # 3. Cria uma lista apenas com os números existentes para comparação fácil
                # (Extrai o valor da chave "numero" de cada item do JSON)
                numeros_existentes = [
                    t.get('numero', '').strip() 
                    for t in telefones_atuais 
                    if isinstance(t, dict)
                ]
                
                # 4. Se o número do plantão NÃO estiver na lista, adicionamos
                if novo_numero not in numeros_existentes:
                    telefones_atuais.append({
                        "tipo": "CELULAR", # Padrão
                        "numero": novo_numero,
                        "observacao": f"Plantão {timezone.now().strftime('%d/%m/%Y')}"
                    })
                    
                    # Salva apenas o campo telefones para ser performático
                    municipe.telefones = telefones_atuais
                    municipe.save(update_fields=['telefones'])
                    
        except Exception as e:
            # Não queremos travar o plantão se der erro na atualização do cadastro
            print(f"Erro ao atualizar JSON de telefones: {e}")

class ContatoEmergencia(models.Model):
    nome = models.CharField(max_length=100) # Ex: Polícia Militar
    telefone = models.CharField(max_length=50) # Ex: (11) 4725-9000
    descricao = models.CharField(max_length=150, blank=True, null=True, help_text="Ex: CPA/M-12")
    ordem = models.IntegerField(default=0, help_text="Menor número aparece primeiro")
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ['ordem', 'nome']
        verbose_name = "Contato de Emergência"
        verbose_name_plural = "Contatos de Emergência"

    def __str__(self):
        return f"{self.nome} - {self.telefone}"