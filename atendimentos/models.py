import uuid
import re
import unicodedata
from django.db import models
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils import timezone

class UppercaseFieldsMixin:
    UPPERCASE_EXCEPTIONS = ('emails', 'endereco', 'dados_etiqueta', 'etiqueta_remetente', 'perfil_ia_texto')

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
    
    class Meta: 
        verbose_name = "Conta"
        verbose_name_plural = "Contas"
        
    def __str__(self): 
        return self.nome
    
    # ========================================
    # PROPRIEDADES PARA MÚLTIPLAS CONTAS GOOGLE
    # ========================================
    
    @property
    def conta_google_padrao(self):
        """
        Retorna a conta Google padrão desta Conta/Gabinete.
        Se não houver padrão definido, retorna a primeira ativa.
        Usado para compatibilidade com código existente.
        """
        # Primeiro tenta buscar a marcada como padrão
        padrao = self.contas_google.filter(ativa=True, eh_padrao=True).first()
        if padrao:
            return padrao
        
        # Se não houver padrão, retorna a primeira ativa
        primeira_ativa = self.contas_google.filter(ativa=True).first()
        return primeira_ativa
    
    def get_contas_google_usuario(self, usuario):
        """
        Retorna as contas Google que o usuário tem permissão de visualizar.
        
        Args:
            usuario (User): Usuário para verificar permissões
            
        Returns:
            QuerySet: Contas Google que o usuário pode acessar
        """
        return self.contas_google.filter(
            ativa=True,
            permissoes_usuarios__usuario=usuario,
            permissoes_usuarios__pode_visualizar=True
        ).distinct()
    
    def usuario_pode_acessar_conta_google(self, usuario, conta_google_id):
        """
        Verifica se o usuário tem permissão para acessar uma conta Google específica.
        
        Args:
            usuario (User): Usuário para verificar
            conta_google_id (int): ID da conta Google
            
        Returns:
            bool: True se tem permissão, False caso contrário
        """
        try:
            permissao = UsuarioContaGooglePermissao.objects.get(
                usuario=usuario,
                conta_google_id=conta_google_id,
                conta_google__conta=self,
                conta_google__ativa=True,
                pode_visualizar=True
            )
            return True
        except UsuarioContaGooglePermissao.DoesNotExist:
            return False
    
    @property
    def total_contas_google(self):
        """Retorna o total de contas Google ativas desta Conta"""
        return self.contas_google.filter(ativa=True).count()
    
    @property
    def tem_multiplas_contas_google(self):
        """Verifica se esta Conta tem múltiplas contas Google configuradas"""
        return self.total_contas_google > 1
    
    def migrar_google_calendar_legado(self):
        """
        Migra o google_calendar_id legado para o novo sistema de múltiplas contas.
        Chamado automaticamente pelo comando de migração.
        
        Returns:
            ContaGoogleCalendar: A conta Google criada ou existente
        """
        if not self.google_calendar_id:
            return None
        
        # Verifica se já existe conta migrada
        conta_existente = self.contas_google.filter(
            email_google=self.google_calendar_id
        ).first()
        
        if conta_existente:
            return conta_existente
        
        # Cria nova conta Google baseada no legado
        conta_google = ContaGoogleCalendar.objects.create(
            conta=self,
            nome="Agenda Principal",
            descricao="Agenda principal migrada do sistema anterior",
            email_google=self.google_calendar_id,
            calendar_id=self.google_calendar_id,
            usar_credenciais_globais=True,
            ativa=True,
            eh_padrao=True
        )
        
        return conta_google


class AutomacaoAniversarioConta(models.Model):
    """
    Configuração de envio de aniversariantes por conta.
    Permite credencial SMTP específica e template próprio por gabinete.
    """
    conta = models.OneToOneField(
        Conta,
        on_delete=models.CASCADE,
        related_name='automacao_aniversario',
        verbose_name="Conta/Gabinete",
    )
    ativo = models.BooleanField(
        default=True,
        verbose_name="Automação ativa?",
        help_text="Se desmarcado, a rotina diária ignora esta conta.",
    )

    # SMTP por conta (credenciais lidas do ambiente via nome da variável).
    smtp_host = models.CharField(
        max_length=255,
        default='cloud77.mailgrid.net.br',
        verbose_name="SMTP host",
    )
    smtp_port = models.PositiveIntegerField(default=587, verbose_name="SMTP porta")
    smtp_use_tls = models.BooleanField(default=True, verbose_name="Usar TLS")
    smtp_use_ssl = models.BooleanField(default=False, verbose_name="Usar SSL")
    env_var_smtp_user = models.CharField(
        max_length=100,
        verbose_name="Variável de ambiente - usuário SMTP",
        help_text="Ex.: SMTP_USER_PREFEITA",
    )
    env_var_smtp_pass = models.CharField(
        max_length=100,
        verbose_name="Variável de ambiente - senha SMTP",
        help_text="Ex.: SMTP_PASS_PREFEITA",
    )
    from_email = models.EmailField(
        blank=True,
        null=True,
        verbose_name="Remetente visível (opcional)",
        help_text="Se vazio, usa o usuário SMTP da conta.",
    )

    # Template por conta
    assunto_template = models.CharField(
        max_length=255,
        default="Feliz aniversário, {{ nome_completo }}!",
        verbose_name="Template de assunto",
        help_text="Suporta placeholders Django Template.",
    )
    corpo_template = models.TextField(
        default=(
            "Prezado(a) {{ nome_completo }},\n\n"
            "Em nome de {{ conta_nome }}, desejamos um feliz aniversário.\n"
            "Saúde, paz e realizações neste novo ciclo."
        ),
        verbose_name="Template do corpo",
        help_text=(
            "Use placeholders como {{ nome_completo }}, {{ conta_nome }}, "
            "{{ conta_nome_titular }} e {{ data_alvo|date:'d/m/Y' }}."
        ),
    )
    arte = models.ImageField(
        upload_to='aniversarios/artes/',
        blank=True,
        null=True,
        verbose_name="Arte de aniversário (inline)",
    )

    # Alerta consolidado para usuários internos (gestores).
    alerta_gestores_ativo = models.BooleanField(
        default=False,
        verbose_name="Alerta para gestores ativo?",
        help_text="Se marcado, envia relatório consolidado de aniversariantes para usuários internos.",
    )
    alerta_categoria = models.CharField(
        max_length=100,
        default="",
        blank=True,
        verbose_name="Categoria para alerta de gestores",
        help_text=(
            "Categoria usada no relatório consolidado. "
            "Se vazio, considera o mailing completo vinculado à conta."
        ),
    )
    alerta_usuarios = models.ManyToManyField(
        User,
        blank=True,
        related_name="alertas_aniversario_recebidos",
        verbose_name="Usuários destinatários do alerta",
        help_text="Selecione os usuários internos que receberão o alerta consolidado.",
    )
    alerta_assunto_template = models.CharField(
        max_length=255,
        default="Relação de aniversariantes de {{ data_aniversario|date:'d/m/Y' }} - {{ conta_nome }}",
        verbose_name="Template de assunto (alerta gestores)",
        help_text="Suporta placeholders Django Template.",
    )
    alerta_corpo_template = models.TextField(
        default=(
            "Prezados,\n\n"
            "Segue relatório consolidado de aniversariantes da categoria {{ categoria }} "
            "para {{ data_aniversario|date:'d/m/Y' }}.\n\n"
            "Conta: {{ conta_nome }}\n"
            "Total de aniversariantes: {{ total_aniversariantes }}\n"
        ),
        verbose_name="Template do corpo (alerta gestores)",
        help_text=(
            "Suporta placeholders Django Template como {{ conta_nome }}, {{ categoria }}, "
            "{{ data_aniversario|date:'d/m/Y' }} e {{ total_aniversariantes }}."
        ),
    )

    data_atualizacao = models.DateTimeField(auto_now=True)
    data_criacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Automação de Aniversário por Conta"
        verbose_name_plural = "Automações de Aniversário por Conta"
        ordering = ['conta__nome']

    @staticmethod
    def _normalizar_sigla(sigla):
        if not sigla:
            return ""
        sem_acentos = unicodedata.normalize("NFKD", sigla).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^A-Z0-9_]", "", sem_acentos.upper())

    @property
    def env_var_smtp_user_esperada(self):
        base = self._normalizar_sigla(getattr(self.conta, "nome_sigla", ""))
        return f"SMTP_USER_{base}" if base else ""

    @property
    def env_var_smtp_pass_esperada(self):
        base = self._normalizar_sigla(getattr(self.conta, "nome_sigla", ""))
        return f"SMTP_PASS_{base}" if base else ""

    def clean(self):
        errors = {}
        if self.env_var_smtp_user and not re.match(r"^SMTP_USER_[A-Z0-9_]+$", self.env_var_smtp_user):
            errors["env_var_smtp_user"] = (
                "Use o padrão SMTP_USER_<SIGLA>, apenas letras maiúsculas, números e underscore."
            )
        if self.env_var_smtp_pass and not re.match(r"^SMTP_PASS_[A-Z0-9_]+$", self.env_var_smtp_pass):
            errors["env_var_smtp_pass"] = (
                "Use o padrão SMTP_PASS_<SIGLA>, apenas letras maiúsculas, números e underscore."
            )

        expected_user = self.env_var_smtp_user_esperada
        expected_pass = self.env_var_smtp_pass_esperada
        if self.conta and self.conta.nome_sigla:
            if self.env_var_smtp_user != expected_user:
                errors["env_var_smtp_user"] = (
                    f"Para a sigla '{self.conta.nome_sigla}', o esperado é '{expected_user}'."
                )
            if self.env_var_smtp_pass != expected_pass:
                errors["env_var_smtp_pass"] = (
                    f"Para a sigla '{self.conta.nome_sigla}', o esperado é '{expected_pass}'."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.conta.nome} - {'ATIVA' if self.ativo else 'INATIVA'}"


class AutomacaoRelatorioDiarioConta(models.Model):
    """
    Envio diário do relatório PDF de atendimentos para usuários internos.
    """
    conta = models.OneToOneField(
        Conta,
        on_delete=models.CASCADE,
        related_name="automacao_relatorio_diario",
        verbose_name="Conta/Gabinete",
    )
    ativo = models.BooleanField(
        default=True,
        verbose_name="Automação ativa?",
        help_text="Se desmarcado, a rotina diária ignora esta conta.",
    )

    smtp_host = models.CharField(
        max_length=255,
        default="cloud77.mailgrid.net.br",
        verbose_name="SMTP host",
    )
    smtp_port = models.PositiveIntegerField(default=587, verbose_name="SMTP porta")
    smtp_use_tls = models.BooleanField(default=True, verbose_name="Usar TLS")
    smtp_use_ssl = models.BooleanField(default=False, verbose_name="Usar SSL")
    env_var_smtp_user = models.CharField(
        max_length=100,
        verbose_name="Variável de ambiente - usuário SMTP",
        help_text="Ex.: SMTP_USER_PREFEITA",
    )
    env_var_smtp_pass = models.CharField(
        max_length=100,
        verbose_name="Variável de ambiente - senha SMTP",
        help_text="Ex.: SMTP_PASS_PREFEITA",
    )
    from_email = models.EmailField(
        blank=True,
        null=True,
        verbose_name="Remetente visível (opcional)",
        help_text="Se vazio, usa o usuário SMTP da conta.",
    )

    destinatarios = models.ManyToManyField(
        User,
        blank=True,
        related_name="relatorios_diarios_recebidos",
        verbose_name="Usuários destinatários",
        help_text="Usuários internos que receberão os relatórios em PDF.",
    )
    assunto_template = models.CharField(
        max_length=255,
        default="Relatórios diários - {{ conta_nome }} - {{ data_referencia|date:'d/m/Y' }}",
        verbose_name="Template de assunto",
        help_text="Suporta placeholders Django Template.",
    )
    corpo_template = models.TextField(
        default=(
            "Prezados,\n\n"
            "Segue em anexo os relatórios do dia {{ data_referencia|date:'d/m/Y' }}.\n\n"
            "Conta: {{ conta_nome }}\n"
        ),
        verbose_name="Template do corpo",
        help_text=(
            "Suporta placeholders como {{ conta_nome }} e "
            "{{ data_referencia|date:'d/m/Y' }}."
        ),
    )
    dias_offset = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="Dias de deslocamento da data",
        help_text=(
            "0 = usa o dia da execução; 1 = usa o dia anterior (útil quando o cron roda de manhã)."
        ),
    )
    enviar_relatorio_atendimentos = models.BooleanField(
        default=True,
        verbose_name="Enviar relatório de atendimentos?",
    )

    data_atualizacao = models.DateTimeField(auto_now=True)
    data_criacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Automação de Relatório Diário por Conta"
        verbose_name_plural = "Automações de Relatório Diário por Conta"
        ordering = ["conta__nome"]

    @property
    def env_var_smtp_user_esperada(self):
        base = AutomacaoAniversarioConta._normalizar_sigla(getattr(self.conta, "nome_sigla", ""))
        return f"SMTP_USER_{base}" if base else ""

    @property
    def env_var_smtp_pass_esperada(self):
        base = AutomacaoAniversarioConta._normalizar_sigla(getattr(self.conta, "nome_sigla", ""))
        return f"SMTP_PASS_{base}" if base else ""

    def clean(self):
        errors = {}
        if self.env_var_smtp_user and not re.match(r"^SMTP_USER_[A-Z0-9_]+$", self.env_var_smtp_user):
            errors["env_var_smtp_user"] = (
                "Use o padrão SMTP_USER_<SIGLA>, apenas letras maiúsculas, números e underscore."
            )
        if self.env_var_smtp_pass and not re.match(r"^SMTP_PASS_[A-Z0-9_]+$", self.env_var_smtp_pass):
            errors["env_var_smtp_pass"] = (
                "Use o padrão SMTP_PASS_<SIGLA>, apenas letras maiúsculas, números e underscore."
            )

        expected_user = self.env_var_smtp_user_esperada
        expected_pass = self.env_var_smtp_pass_esperada
        if self.conta and self.conta.nome_sigla:
            if self.env_var_smtp_user != expected_user:
                errors["env_var_smtp_user"] = (
                    f"Para a sigla '{self.conta.nome_sigla}', o esperado é '{expected_user}'."
                )
            if self.env_var_smtp_pass != expected_pass:
                errors["env_var_smtp_pass"] = (
                    f"Para a sigla '{self.conta.nome_sigla}', o esperado é '{expected_pass}'."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.conta.nome} - relatórios diários - {'ATIVA' if self.ativo else 'INATIVA'}"


class CampanhaEmail(models.Model):
    STATUS_CHOICES = [
        ("RASCUNHO", "Rascunho"),
        ("AGENDADA", "Agendada"),
        ("PROCESSANDO", "Processando"),
        ("CONCLUIDA", "Concluída"),
        ("ERRO", "Erro"),
        ("CANCELADA", "Cancelada"),
    ]
    TIPO_DISPARO_CHOICES = [
        ("IMEDIATO", "Imediato"),
        ("AGENDADO", "Agendado"),
    ]

    conta = models.ForeignKey(
        Conta,
        on_delete=models.PROTECT,
        related_name="campanhas_email",
        verbose_name="Conta/Gabinete",
    )
    nome = models.CharField(max_length=200, verbose_name="Nome da Campanha")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="RASCUNHO", db_index=True)
    tipo_disparo = models.CharField(max_length=10, choices=TIPO_DISPARO_CHOICES, default="IMEDIATO")
    data_hora_disparo = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Data/Hora de Disparo",
        help_text="Obrigatório quando o tipo de disparo for AGENDADO.",
    )
    disparo_solicitado_em = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Solicitado para fila em",
    )
    email_teste = models.EmailField(
        blank=True,
        null=True,
        verbose_name="E-mail de teste (opcional)",
        help_text="Se preenchido, todos os envios serão direcionados para este endereço.",
    )
    categorias = models.ManyToManyField(
        "CategoriaContato",
        blank=True,
        related_name="campanhas_email",
        verbose_name="Categorias de Contato",
        help_text="Se vazio, considera o mailing completo da conta.",
    )
    assunto_template = models.CharField(
        max_length=255,
        verbose_name="Template de Assunto",
        help_text="Suporta placeholders Django Template (ex.: {{ nome_completo }}).",
    )
    corpo_template = models.TextField(
        verbose_name="Template do Corpo",
        help_text="Suporta placeholders Django Template.",
    )
    arte = models.ImageField(
        upload_to="campanhas/artes/",
        blank=True,
        null=True,
        verbose_name="Imagem no corpo (inline)",
    )
    anexo = models.FileField(
        upload_to="campanhas/anexos/",
        blank=True,
        null=True,
        verbose_name="Anexo de campanha",
    )
    criado_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campanhas_email_criadas",
    )
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)
    ultima_execucao_em = models.DateTimeField(null=True, blank=True)
    total_destinatarios = models.PositiveIntegerField(default=0)
    total_enviados = models.PositiveIntegerField(default=0)
    total_falhas = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Campanha de E-mail"
        verbose_name_plural = "Campanhas de E-mail"
        ordering = ["-data_criacao"]

    def __str__(self):
        return f"{self.nome} ({self.conta.nome})"


class CampanhaDestinatario(models.Model):
    campanha = models.ForeignKey(
        CampanhaEmail,
        on_delete=models.CASCADE,
        related_name="destinatarios",
        verbose_name="Campanha",
    )
    municipe = models.ForeignKey(
        "Municipe",
        on_delete=models.CASCADE,
        related_name="campanha_destinatarios",
        verbose_name="Munícipe",
    )
    conta = models.ForeignKey(
        Conta,
        on_delete=models.PROTECT,
        related_name="campanha_destinatarios",
        verbose_name="Conta",
    )
    categoria = models.ForeignKey(
        "CategoriaContato",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campanha_destinatarios",
    )
    email_destino = models.EmailField(verbose_name="E-mail de destino")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Destinatário da Campanha"
        verbose_name_plural = "Destinatários da Campanha"
        ordering = ["municipe__nome_completo"]
        unique_together = ("campanha", "municipe")

    def __str__(self):
        return f"{self.municipe.nome_completo} -> {self.email_destino}"


class CampanhaLogEnvio(models.Model):
    STATUS_CHOICES = [
        ("SUCESSO", "Sucesso"),
        ("FALHA", "Falha"),
    ]

    campanha = models.ForeignKey(
        CampanhaEmail,
        on_delete=models.CASCADE,
        related_name="logs_envio",
        verbose_name="Campanha",
    )
    destinatario = models.ForeignKey(
        CampanhaDestinatario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs",
        verbose_name="Destinatário",
    )
    email_real_enviado = models.EmailField(
        blank=True,
        null=True,
        verbose_name="E-mail real enviado",
        help_text="Destinatário efetivo no SMTP (considera e-mail de teste, quando ativo).",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, db_index=True)
    erro = models.TextField(blank=True, verbose_name="Detalhes do erro")
    data_envio = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Log de Envio da Campanha"
        verbose_name_plural = "Logs de Envio da Campanha"
        ordering = ["-data_envio"]

    def __str__(self):
        return f"{self.campanha.nome} - {self.status} ({self.data_envio:%d/%m/%Y %H:%M})"

class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    contas = models.ManyToManyField(Conta, blank=True)
    categorias_contato = models.ManyToManyField(
        'CategoriaContato',
        blank=True,
        related_name='operadores_crm',
        verbose_name='Categorias de contato (Operador CRM)',
        help_text='Escopo de categorias para usuários do grupo Operador CRM.',
    )
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
        verbose_name="Pronome de Tratamento (legado)",
        help_text="Ex: Senhor, Senhora, Dr., Dra., Vossa Excelência. Migrado para PerfilMunicipe.",
    )
    nome_de_guerra = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        verbose_name="Nome de Guerra / Apelido"
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
    # Campos legados: mantidos temporariamente; dados migrados para PerfilMunicipe (ver migração 0034).
    # Após garantir que todo o código lê de perfis, aplicar migração de remoção (0035).
    cargo = models.CharField(max_length=150, blank=True, null=True, verbose_name="Cargo (legado)")
    orgao = models.CharField(max_length=150, blank=True, null=True, verbose_name="Órgão/Empresa (legado)")
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
    perfil_ia_texto = models.TextField(
        blank=True,
        null=True,
        verbose_name="Texto consolidado para vetor IA",
        help_text="Texto usado para gerar o embedding (debug)."
    )
    vetor_ia_perfil = models.JSONField(
        null=True,
        blank=True,
        verbose_name="Vetor IA Perfil (Embedding)",
        help_text="Embedding para busca semântica do perfil."
    )
    auditoria_ia_data = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Última atualização IA",
        help_text="Data/hora da última geração do vetor IA."
    )
    class Meta: verbose_name = "Munícipe"; verbose_name_plural = "Munícipes"; ordering = ['nome_completo']
    def __str__(self): return self.nome_completo


class PerfilMunicipe(UppercaseFieldsMixin, models.Model):
    """
    Perfil do munícipe por conta (cargo/órgão). Permite múltiplos cargos/órgãos
    para a mesma pessoa, evitando duplicidade de registros.
    """
    municipe = models.ForeignKey(
        Municipe,
        on_delete=models.CASCADE,
        related_name='perfis',
        verbose_name="Munícipe"
    )
    conta = models.ForeignKey(
        Conta,
        on_delete=models.PROTECT,
        related_name='perfis_municipe',
        verbose_name="Conta/Gabinete"
    )
    categoria = models.ForeignKey(
        CategoriaContato,
        on_delete=models.PROTECT,
        related_name='perfis_municipe',
        verbose_name="Categoria do Contato"
    )
    cargo = models.CharField(max_length=150, blank=True, null=True, verbose_name="Cargo")
    instituicao = models.CharField(max_length=150, blank=True, null=True, verbose_name="Instituição/Órgão")
    departamento = models.CharField(max_length=150, blank=True, null=True, verbose_name="Departamento")
    tratamento = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Tratamento",
        help_text="Ex: Sr., Dr., Vossa Excelência"
    )
    ativo = models.BooleanField(default=True, verbose_name="Ativo")

    class Meta:
        verbose_name = "Perfil do Munícipe"
        verbose_name_plural = "Perfis do Munícipe"
        ordering = ['conta', 'cargo']

    def __str__(self):
        return f"{self.municipe.nome_completo} — {self.cargo or '-'} @ {self.instituicao or '-'}"


class CategoriaAtendimento(UppercaseFieldsMixin, models.Model):
    nome = models.CharField(max_length=100, unique=True, verbose_name="Nome da Categoria")
    descricao = models.TextField(blank=True, null=True, verbose_name="Descrição")
    ativa = models.BooleanField(default=True, verbose_name="Está ativa?")
    class Meta: verbose_name = "Categoria de Atendimento"; verbose_name_plural = "Categorias de Atendimento"; ordering = ['nome']
    def __str__(self): return self.nome


class AssuntoAtendimento(UppercaseFieldsMixin, models.Model):
    """
    Taxonomia principal do tema do atendimento (saúde, educação, etc.).
    Distinta de CategoriaAtendimento (tags complementares M2M).
    """
    UPPERCASE_EXCEPTIONS = ('codigo', 'descricao')

    nome = models.CharField(max_length=100, unique=True, verbose_name="Nome do Assunto")
    codigo = models.SlugField(
        max_length=50,
        unique=True,
        verbose_name="Código",
        help_text="Identificador estável (ex.: saude, educacao) para IA e integrações.",
    )
    descricao = models.TextField(blank=True, null=True, verbose_name="Descrição")
    ativo = models.BooleanField(default=True, verbose_name="Ativo?")
    ordem = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="Ordem de exibição",
        help_text="Menor valor aparece primeiro nas listas.",
    )

    class Meta:
        verbose_name = "Assunto de Atendimento"
        verbose_name_plural = "Assuntos de Atendimento"
        ordering = ['ordem', 'nome']

    def __str__(self):
        return self.nome


class SlaAtendimentoConfig(models.Model):
    """Prazos SLA por conta e assunto (dias úteis). Assunto nulo = padrão da conta."""
    conta = models.ForeignKey(
        Conta,
        on_delete=models.CASCADE,
        related_name='slas_atendimento',
        verbose_name="Conta/Gabinete",
    )
    assunto = models.ForeignKey(
        AssuntoAtendimento,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='slas_config',
        verbose_name="Assunto",
        help_text="Vazio = regra padrão do gabinete.",
    )
    dias_resposta = models.PositiveSmallIntegerField(
        default=3,
        verbose_name="Dias úteis para resposta",
    )
    dias_conclusao = models.PositiveSmallIntegerField(
        default=10,
        verbose_name="Dias úteis para conclusão",
    )
    ativo = models.BooleanField(default=True, verbose_name="Ativo")

    class Meta:
        verbose_name = "Configuração SLA de Atendimento"
        verbose_name_plural = "Configurações SLA de Atendimento"
        constraints = [
            models.UniqueConstraint(
                fields=['conta', 'assunto'],
                name='uniq_sla_conta_assunto',
            ),
        ]
        ordering = ['conta', 'assunto']

    def __str__(self):
        assunto = self.assunto.nome if self.assunto_id else 'Padrão'
        return f"{self.conta.nome} — {assunto} ({self.dias_conclusao}d conclusão)"


class Atendimento(models.Model):
    STATUS_CHOICES = [('ABERTO', 'Aberto'), ('EM_ANALISE', 'Em Análise'), ('ENCAMINHADO', 'Encaminhado'), ('CONCLUIDO', 'Concluído'), ('ARQUIVADO', 'Arquivado')]
    ORIGEM_CHOICES = [
        ('PRESENCIAL', 'Presencial'),
        ('TELEFONE', 'Telefone'),
        ('EMAIL', 'E-mail'),
        ('WHATSAPP', 'WhatsApp'),
    ]
    protocolo = models.CharField(max_length=20, unique=True, blank=True, editable=False, verbose_name="Protocolo")
    origem = models.CharField(max_length=20, choices=ORIGEM_CHOICES, default='PRESENCIAL', verbose_name="Origem do Atendimento")
    titulo = models.CharField(max_length=255, verbose_name="Título do Atendimento")
    descricao = models.TextField(verbose_name="Descrição Detalhada")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ABERTO', verbose_name="Status")
    categorias = models.ManyToManyField(CategoriaAtendimento, blank=True, related_name="atendimentos", verbose_name="Categorias")
    assunto = models.ForeignKey(
        AssuntoAtendimento,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='atendimentos',
        verbose_name="Assunto",
        help_text="Classificação principal do tema (saúde, educação, visita, etc.).",
    )
    assunto_ia_sugerido = models.ForeignKey(
        AssuntoAtendimento,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='atendimentos_sugestao_ia',
        verbose_name="Assunto sugerido pela IA",
    )
    ASSUNTO_IA_STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('APLICADO', 'Aplicado automaticamente'),
        ('REVISADO', 'Revisado manualmente'),
        ('ERRO', 'Erro'),
    ]
    assunto_ia_status = models.CharField(
        max_length=20,
        choices=ASSUNTO_IA_STATUS_CHOICES,
        null=True,
        blank=True,
        verbose_name="Status classificação IA (assunto)",
        help_text="Preenchido quando a rotina de IA processar o atendimento.",
    )
    conta = models.ForeignKey(Conta, on_delete=models.PROTECT, related_name='atendimentos', verbose_name="Conta/Gabinete")
    municipe = models.ForeignKey(Municipe, on_delete=models.PROTECT, related_name='atendimentos', verbose_name="Munícipe")
    responsavel = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='atendimentos_responsaveis', verbose_name="Responsável")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='atendimentos_criados')
    data_criacao = models.DateTimeField(auto_now_add=True, verbose_name="Data de Criação")
    data_atualizacao = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")
    resumo_ia = models.TextField(blank=True, null=True, verbose_name="Resumo Gerado por IA", help_text="Resumo automático gerado pelo Gemini AI (legado)")
    resumo_ia_local = models.TextField(
        blank=True,
        null=True,
        verbose_name="Resumo IA Local (Ollama)",
        help_text="Resumo consolidado gerado pela IA local considerando triagem e tramitações."
    )
    vetor_ia_atendimento = models.JSONField(
        null=True,
        blank=True,
        verbose_name="Vetor IA (Embedding)",
        help_text="Embedding para busca semântica (mxbai-embed-large)."
    )
    auditoria_ia_status = models.CharField(
        max_length=20,
        default='PENDENTE',
        verbose_name="Status Processamento IA",
        choices=[('PENDENTE', 'Pendente'), ('PROCESSADO', 'Processado'), ('ERRO', 'Erro')]
    )
    responsaveis_compartilhados = models.ManyToManyField(
        User,
        blank=True,
        related_name='atendimentos_compartilhados',
        verbose_name="Co-responsáveis (compartilhado)",
        help_text="Usuários que também podem gerir este atendimento (compartilhamento)."
    )
    prazo_resposta = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Prazo de resposta (SLA)",
    )
    prazo_conclusao = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Prazo de conclusão (SLA)",
    )
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
    STATUS_AGENDA_CHOICES = [('SOLICITADO', 'Solicitado'), ('EM_ANALISE', 'Em Análise'), ('AGENDADO', 'Agendado'), ('AGENDAR', 'Agendar'), ('NEGADO', 'Negado'), ('CANCELADO', 'Cancelado'), ('REAGENDAR', 'Reagendar'), ('ENCAMINHADO', 'Encaminhado'), ('CONCLUIDO', 'Concluido')]
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
    ACAO_CHOICES = [
        ('CRIACAO', 'Criação de Atendimento'),
        ('EDICAO', 'Edição de Atendimento'),
        ('DELECAO', 'Deleção de Atendimento'),
        ('TRAMITACAO', 'Nova Tramitação'),
        ('EDICAO_TRAMITACAO', 'Edição de Tramitação'),
        ('DELECAO_TRAMITACAO', 'Deleção de Tramitação'),
        ('MUNICIPE_CRIACAO', 'Criação de Munícipe'),
        ('MUNICIPE_EDICAO', 'Edição de Munícipe'),
        ('MUNICIPE_DELECAO', 'Deleção de Munícipe'),
        ('PERFIL_CRIACAO', 'Criação de Perfil do Munícipe'),
        ('PERFIL_EDICAO', 'Edição de Perfil do Munícipe'),
        ('PERFIL_DELECAO', 'Deleção de Perfil do Munícipe'),
        ('CATEGORIA_CRIACAO', 'Criação de Categoria de Contato'),
        ('CATEGORIA_EDICAO', 'Edição de Categoria de Contato'),
        ('CATEGORIA_DESATIVACAO', 'Desativação de Categoria de Contato'),
        ('MESCLAGEM', 'Mesclagem de Duplicatas'),
    ]
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Usuário")
    acao = models.CharField(max_length=30, choices=ACAO_CHOICES, verbose_name="Ação Realizada")
    detalhes = models.TextField(verbose_name="Detalhes do Log")
    conta = models.ForeignKey(
        Conta,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs_crm',
        verbose_name="Conta/Gabinete",
    )
    payload = models.JSONField(default=dict, blank=True, verbose_name="Payload (diff resumido)")
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

# ========================================
# MÚLTIPLAS CONTAS GOOGLE CALENDAR - FASE 1
# ========================================

class ContaGoogleCalendar(models.Model):
    """
    Múltiplas contas Google Calendar por gabinete.
    Permite que uma Conta tenha várias agendas Google configuradas.
    """
    conta = models.ForeignKey(
        Conta, 
        on_delete=models.CASCADE, 
        related_name='contas_google',
        verbose_name="Gabinete/Conta"
    )
    
    # Identificação
    nome = models.CharField(
        max_length=100, 
        help_text="Nome identificador (ex: 'Agenda Pública', 'Agenda Privada')",
        verbose_name="Nome da Conta Google"
    )
    descricao = models.TextField(
        blank=True, 
        help_text="Descrição detalhada desta conta Google",
        verbose_name="Descrição"
    )
    
    # Dados Google
    email_google = models.EmailField(
        help_text="Email da conta Google (@gmail.com ou domínio personalizado)",
        verbose_name="Email da Conta Google"
    )
    calendar_id = models.EmailField(
        help_text="ID do Google Calendar (normalmente igual ao email)",
        verbose_name="ID do Google Calendar"
    )
    
    # Credenciais OAuth (podem ser as mesmas globais ou específicas)
    client_id = models.CharField(
        max_length=255, 
        blank=True,
        help_text="Client ID específico (deixe vazio para usar global do .env)",
        verbose_name="Client ID OAuth"
    )
    client_secret = models.CharField(
        max_length=255, 
        blank=True,
        help_text="Client Secret específico (deixe vazio para usar global do .env)",
        verbose_name="Client Secret OAuth"
    )
    usar_credenciais_globais = models.BooleanField(
        default=True, 
        help_text="Se marcado, usa credenciais do arquivo .env (GOOGLE_CLIENT_ID/SECRET)",
        verbose_name="Usar Credenciais Globais"
    )
    
    # Status e configurações
    ativa = models.BooleanField(
        default=True,
        verbose_name="Conta Ativa"
    )
    eh_padrao = models.BooleanField(
        default=False, 
        help_text="Conta Google padrão para esta Conta/Gabinete",
        verbose_name="É Conta Padrão"
    )
    
    # Metadados
    data_criacao = models.DateTimeField(auto_now_add=True, verbose_name="Data de Criação")
    data_atualizacao = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")
    
    class Meta:
        verbose_name = "Conta Google Calendar"
        verbose_name_plural = "Contas Google Calendar"
        unique_together = [['conta', 'nome'], ['conta', 'email_google']]
        ordering = ['conta__nome', 'nome']
        
    def __str__(self):
        padrao_str = " [PADRÃO]" if self.eh_padrao else ""
        return f"{self.conta.nome} - {self.nome}{padrao_str}"
    
    def get_client_id(self):
        """Retorna client_id específico ou global do .env"""
        if self.usar_credenciais_globais or not self.client_id:
            from django.conf import settings
            return settings.GOOGLE_CLIENT_ID
        return self.client_id
    
    def get_client_secret(self):
        """Retorna client_secret específico ou global do .env"""
        if self.usar_credenciais_globais or not self.client_secret:
            from django.conf import settings
            return settings.GOOGLE_CLIENT_SECRET
        return self.client_secret
    
    def clean(self):
        """Validações customizadas"""
        from django.core.exceptions import ValidationError
        
        # Valida que só pode haver uma conta padrão por Conta
        if self.eh_padrao:
            existing_default = ContaGoogleCalendar.objects.filter(
                conta=self.conta, 
                eh_padrao=True
            ).exclude(pk=self.pk)
            
            if existing_default.exists():
                raise ValidationError({
                    'eh_padrao': 'Já existe uma conta Google padrão para este gabinete.'
                })
        
        # Se não usar credenciais globais, client_id e client_secret são obrigatórios
        if not self.usar_credenciais_globais:
            if not self.client_id:
                raise ValidationError({
                    'client_id': 'Client ID é obrigatório quando não usar credenciais globais.'
                })
            if not self.client_secret:
                raise ValidationError({
                    'client_secret': 'Client Secret é obrigatório quando não usar credenciais globais.'
                })


class UsuarioContaGooglePermissao(models.Model):
    """
    Controle de acesso: qual usuário pode usar qual conta Google.
    Sistema granular de permissões por usuário e conta Google específica.
    """
    usuario = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='permissoes_google',
        verbose_name="Usuário"
    )
    conta_google = models.ForeignKey(
        ContaGoogleCalendar, 
        on_delete=models.CASCADE, 
        related_name='permissoes_usuarios',
        verbose_name="Conta Google Calendar"
    )
    
    # Permissões específicas
    pode_visualizar = models.BooleanField(
        default=True, 
        help_text="Pode ver eventos desta agenda no sistema",
        verbose_name="Pode Visualizar"
    )
    pode_criar = models.BooleanField(
        default=False, 
        help_text="Pode criar novos eventos nesta agenda",
        verbose_name="Pode Criar Eventos"
    )
    pode_editar = models.BooleanField(
        default=False, 
        help_text="Pode editar eventos existentes desta agenda",
        verbose_name="Pode Editar Eventos"
    )
    pode_excluir = models.BooleanField(
        default=False, 
        help_text="Pode excluir eventos desta agenda",
        verbose_name="Pode Excluir Eventos"
    )
    
    # Metadados
    data_criacao = models.DateTimeField(auto_now_add=True, verbose_name="Data de Criação")
    criado_por = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='permissoes_google_criadas',
        verbose_name="Criado por"
    )
    
    class Meta:
        verbose_name = "Permissão Google Calendar"
        verbose_name_plural = "Permissões Google Calendar"
        unique_together = [['usuario', 'conta_google']]
        ordering = ['conta_google__conta__nome', 'conta_google__nome', 'usuario__username']
        
    def __str__(self):
        nome_usuario = self.usuario.get_full_name() or self.usuario.username
        return f"{nome_usuario} → {self.conta_google.nome}"
    
    @property
    def nivel_acesso(self):
        """Retorna nível de acesso resumido"""
        if self.pode_excluir:
            return "Completo"
        elif self.pode_editar:
            return "Edição"
        elif self.pode_criar:
            return "Criação"
        elif self.pode_visualizar:
            return "Visualização"
        else:
            return "Sem Acesso"


def _as_aware_datetime(value):
    """Normaliza datetime para aware (UTC) — evita TypeError em tokens OAuth legados."""
    from datetime import timezone as dt_timezone

    from django.utils import timezone as tz

    if value is None:
        return None
    if tz.is_naive(value):
        return tz.make_aware(value, dt_timezone.utc)
    return value


class TokenGoogleCalendar(models.Model):
    """
    Tokens OAuth por usuário e conta Google específica.
    Substitui o modelo GoogleApiToken antigo com suporte a múltiplas contas.
    """
    usuario = models.ForeignKey(
        User, 
        on_delete=models.CASCADE,
        verbose_name="Usuário"
    )
    conta_google = models.ForeignKey(
        ContaGoogleCalendar, 
        on_delete=models.CASCADE,
        verbose_name="Conta Google Calendar"
    )
    
    # Tokens OAuth
    access_token = models.TextField(verbose_name="Access Token")
    refresh_token = models.TextField(verbose_name="Refresh Token")
    expires_at = models.DateTimeField(verbose_name="Data de Expiração")
    
    # Metadados
    data_criacao = models.DateTimeField(auto_now_add=True, verbose_name="Data de Criação")
    data_atualizacao = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")
    ultima_renovacao = models.DateTimeField(
        null=True, 
        blank=True,
        verbose_name="Última Renovação do Token"
    )
    
    class Meta:
        verbose_name = "Token Google Calendar"
        verbose_name_plural = "Tokens Google Calendar"
        unique_together = [['usuario', 'conta_google']]
        ordering = ['-data_atualizacao']
        
    def __str__(self):
        status = "EXPIRADO" if self.is_expired else "VÁLIDO"
        return f"Token {self.usuario.username} - {self.conta_google.nome} [{status}]"
    
    @property
    def is_expired(self):
        """Verifica se o token está expirado"""
        from django.utils import timezone

        if not self.expires_at:
            return True
        expires_at = _as_aware_datetime(self.expires_at)
        return timezone.now() >= expires_at

    @property
    def dias_para_expirar(self):
        """Retorna quantos dias faltam para expirar (negativo se já expirou)"""
        from django.utils import timezone

        if not self.expires_at:
            return 0
        expires_at = _as_aware_datetime(self.expires_at)
        return (expires_at - timezone.now()).days
    
    def marcar_renovacao(self):
        """Marca a data da última renovação"""
        from django.utils import timezone
        self.ultima_renovacao = timezone.now()
        self.save(update_fields=['ultima_renovacao'])


# ========================================
# MODELO LEGADO - COMPATIBILIDADE
# ========================================

class GoogleApiToken(models.Model):
    """
    MODELO LEGADO - Mantido para compatibilidade durante migração.
    Usar TokenGoogleCalendar para novos desenvolvimentos.
    """
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='google_token_legado')
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    
    # Flag de migração
    migrado = models.BooleanField(
        default=False,
        help_text="Indica se este token já foi migrado para o novo sistema"
    )
    data_migracao = models.DateTimeField(
        null=True, 
        blank=True,
        help_text="Data em que foi migrado para TokenGoogleCalendar"
    )

    def __str__(self):
        status = "[MIGRADO]" if self.migrado else "[LEGADO]"
        return f"Token Legado {self.usuario.username} {status}"
    
    class Meta:
        verbose_name = "Token Google (Legado)"
        verbose_name_plural = "Tokens Google (Legados)"


class RegistroVisita(UppercaseFieldsMixin, models.Model):
    """
    Modelo para registrar um check-in/visita rápida,
    sem a complexidade de um Atendimento.
    """
    municipe = models.ForeignKey(Municipe, on_delete=models.CASCADE, related_name="visitas")
    conta_destino = models.ForeignKey(Conta, on_delete=models.PROTECT, verbose_name="Gabinete de Destino")
    usuario_destino = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="visitas_destino", verbose_name="Responsável / Usuário Destino"
    )
    data_checkin = models.DateTimeField(auto_now_add=True, verbose_name="Data do Check-in")
    observacao = models.TextField(blank=True, null=True, verbose_name="Observação")
    registrado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="visitas_registradas")
    atendimento = models.ForeignKey(
        Atendimento,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='registros_visita_legado',
        verbose_name='Atendimento vinculado',
        help_text='Atendimento unificado gerado a partir desta visita (migração Fase 4).',
    )

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