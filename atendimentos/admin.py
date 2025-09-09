import re
import string
import secrets
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.template.loader import render_to_string
from import_export import resources
from import_export.fields import Field
from import_export.widgets import ForeignKeyWidget, ManyToManyWidget
from import_export.admin import ImportExportModelAdmin
from datetime import datetime
from .models import (
    Conta, Municipe, Atendimento, Tramitacao, CategoriaAtendimento, ReservaEspaco,
    SolicitacaoAgenda, Anexo, LogDeAtividade, PerfilUsuario, Notificacao, CategoriaContato, Espaco, RegistroVisita, Lembrete
)

def enviar_email_de_acesso(modeladmin, request, queryset):
    """
    Ação do Django Admin para gerar uma nova senha e enviar as credenciais por e-mail.
    """
    cont_enviados = 0
    for user in queryset:
        if not user.email:
            messages.warning(request, f"O usuário '{user.username}' não possui e-mail e não foi notificado.")
            continue

        try:
            # --- A CORREÇÃO ESTÁ AQUI ---
            # 1. Gera uma senha forte e aleatória usando as ferramentas do Python
            alfabeto = string.ascii_letters + string.digits
            senha_provisoria = ''.join(secrets.choice(alfabeto) for i in range(10))
            # --- FIM DA CORREÇÃO ---
            
            # 2. Define a nova senha para o usuário (o Django cuida da criptografia)
            user.set_password(senha_provisoria)
            user.save()

            # 3. Prepara e envia o e-mail usando nosso template
            contexto = {
                'nome_usuario': user.get_full_name() or user.username,
                'username': user.username,
                'senha_provisoria': senha_provisoria,
                'link_sistema': 'https://gabinete.mogidascruzes.sp.gov.br' 
            }
            html_message = render_to_string('emails/envio_credenciais.html', contexto)
            
            send_mail(
                subject='Suas Credenciais de Acesso ao Sistema SIGA Gabinete',
                message=f"Seu usuário é {user.username} e sua senha provisória é {senha_provisoria}",
                from_email='nao-responda@mogidascruzes.sp.gov.br',
                recipient_list=[user.email],
                html_message=html_message
            )
            cont_enviados += 1
        except Exception as e:
            messages.error(request, f"Falha ao enviar e-mail para '{user.username}': {e}")

    if cont_enviados > 0:
        messages.success(request, f"{cont_enviados} e-mail(s) de acesso enviados com sucesso!")

enviar_email_de_acesso.short_description = "Enviar E-mail de Acesso com Senha Provisória"


# --- Configuração do Perfil de Usuário no Admin ---
class PerfilUsuarioInline(admin.StackedInline):
    model = PerfilUsuario
    can_delete = False
    verbose_name_plural = 'Perfil de Vínculos'
    fk_name = 'usuario'
    filter_horizontal = ('contas',)
    fields = ('contas', 'pode_visualizar_agendas_compartilhadas')

class UserAdmin(BaseUserAdmin):
    inlines = (PerfilUsuarioInline,)
    fieldsets = BaseUserAdmin.fieldsets
    add_fieldsets = BaseUserAdmin.add_fieldsets
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff')
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'groups')
    actions = [enviar_email_de_acesso]

admin.site.unregister(User)
admin.site.register(User, UserAdmin)


# --- Admin para o modelo Conta ---
@admin.register(Conta)
class ContaAdmin(admin.ModelAdmin):
    list_display = ('id', 'nome', 'nome_instituicao')
    search_fields = ['nome', 'nome_instituicao']
    fieldsets = (
        (None, {
            'fields': ('nome_instituicao', 'nome')
        }),
        ('Personalização e Integrações', {
            'fields': ('brasao_instituicao', 'logo_conta', 'google_calendar_id')
        }),
    )


# --- Configuração de Importação/Exportação para Munícipe ---
class MunicipeResource(resources.ModelResource):
    # --- MAPEAMENTO DOS CAMPOS (JÁ ESTAVA CORRETO) ---
    categoria = Field(
        column_name='categoria',
        attribute='categoria',
        widget=ForeignKeyWidget(CategoriaContato, 'nome'))

    contas = Field(
        column_name='gabinete_proprietario', # Nome da coluna no seu arquivo CSV
        attribute='contas',
        widget=ManyToManyWidget(Conta, separator=',', field='nome'))

    # --- MÉTODO PARA PREPARAR OS DADOS DA LINHA (JÁ ESTAVA CORRETO) ---
    def before_import_row(self, row, **kwargs):
        if 'data_nascimento' in row and row['data_nascimento']:
            try:
                data_obj = datetime.strptime(row['data_nascimento'], '%d/%m/%Y')
                row['data_nascimento'] = data_obj.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                row['data_nascimento'] = None

        if 'telefones' in row and row['telefones']:
            numeros_limpos = re.sub(r'\D', '', str(row['telefones']))
            telefone_formatado = str(row['telefones']) # Esta linha agora é redundante, mas não prejudica
            
            if len(numeros_limpos) == 11:
                telefone_formatado = f"({numeros_limpos[:2]}) {numeros_limpos[2:7]}-{numeros_limpos[7:]}"
            elif len(numeros_limpos) == 10:
                telefone_formatado = f"({numeros_limpos[:2]}) {numeros_limpos[2:6]}-{numeros_limpos[6:]}"
            else:
                 telefone_formatado = numeros_limpos 
            row['telefones'] = f'[{{"tipo": "principal", "numero": "{telefone_formatado}"}}]'
        else:
            row['telefones'] = '[]'
            
    # --- O CORAÇÃO DA IMPORTAÇÃO INTELIGENTE (MÉTODO ATUALIZADO) ---
    def get_instance(self, instance_loader, row):
        """
        Lógica para encontrar um munícipe existente ou decidir criar um novo.
        A verificação segue uma ordem de prioridade para evitar duplicatas.
        """
        # Prioridade 1: CPF (o identificador mais forte)
        cpf = row.get('cpf')
        if cpf and str(cpf).strip():
            try:
                return Municipe.objects.get(cpf=str(cpf).strip())
            except Municipe.DoesNotExist:
                pass # Se não achar por CPF, continua para a próxima verificação

        # Prioridade 2: Email (segundo identificador mais forte)
        email = row.get('email')
        if email and str(email).strip():
            try:
                return Municipe.objects.get(email__iexact=str(email).strip())
            except Municipe.DoesNotExist:
                pass # Se não achar por email, continua

        # Prioridade 3: Nome Completo (última tentativa de evitar duplicata)
        nome_completo = row.get('nome_completo')
        if nome_completo and str(nome_completo).strip():
            try:
                # Tenta encontrar por nome exato (ignorando maiúsculas/minúsculas)
                return Municipe.objects.get(nome_completo__iexact=str(nome_completo).strip())
            except Municipe.DoesNotExist:
                pass

        # Se nenhuma das verificações encontrou um registro, retorna None.
        # Isso sinaliza para a ferramenta que um NOVO contato deve ser criado.
        return None

    class Meta:
        model = Municipe
        skip_unchanged = True
        report_skipped = True
        # Removemos 'import_id_fields' para dar controle total ao 'get_instance'
        fields = ('id', 'nome_completo', 'cpf', 'data_nascimento', 'email', 'telefones', 'cargo', 'orgao', 'categoria', 'contas')
        export_order = fields


# --- Admin para o modelo Munícipe ---
@admin.register(Municipe)
class MunicipeAdmin(ImportExportModelAdmin):
    resource_class = MunicipeResource
    list_display = ('nome_completo', 'tratamento', 'cpf', 'get_email_principal', 'get_telefone_principal', 'categoria', 'listar_contas')
    search_fields = ('nome_completo', 'cpf', 'emails__email')
    list_filter = ('categoria', 'contas')
    filter_horizontal = ('contas',)

    def get_telefone_principal(self, obj):
        if obj.telefones and len(obj.telefones) > 0:
            return obj.telefones[0].get('numero')
        return "N/A"
    get_telefone_principal.short_description = 'Telefone'

    def get_email_principal(self, obj):
        if obj.emails and len(obj.emails) > 0:
            return obj.emails[0].get('email')
        return "N/A"
    get_email_principal.short_description = 'Email Principal'

    def listar_contas(self, obj):
        return ", ".join([conta.nome for conta in obj.contas.all()])
    listar_contas.short_description = 'Contas'


# --- Admin para outros modelos ---
@admin.register(Atendimento)
class AtendimentoAdmin(admin.ModelAdmin):
    list_display = ('protocolo', 'titulo', 'municipe', 'conta', 'status', 'data_criacao')
    list_filter = ('status', 'conta', 'data_criacao', 'categorias')
    search_fields = ('protocolo', 'titulo', 'municipe__nome_completo')
    filter_horizontal = ('categorias',)
    
@admin.register(Tramitacao)
class TramitacaoAdmin(admin.ModelAdmin):
    list_display = ('atendimento', 'usuario', 'data_tramitacao')

@admin.register(LogDeAtividade)
class LogDeAtividadeAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'usuario', 'acao', 'detalhes')
    list_filter = ('acao', 'timestamp', 'usuario')
    search_fields = ('detalhes', 'usuario__username')

admin.site.register(SolicitacaoAgenda)
admin.site.register(Anexo)
admin.site.register(CategoriaAtendimento)

@admin.register(CategoriaContato)
class CategoriaContatoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativa')
    search_fields = ('nome',)

@admin.register(Espaco)
class EspacoAdmin(admin.ModelAdmin):
    """
    Configuração para exibir o modelo Espaco na área administrativa.
    """
    list_display = ('nome', 'capacidade', 'ativo')
    list_filter = ('ativo', 'contas')
    search_fields = ('nome', 'descricao')
    filter_horizontal = ('contas',)


@admin.register(RegistroVisita)
class RegistroVisitaAdmin(admin.ModelAdmin):
    """
    Configuração do painel Admin para o modelo de Registro de Visitas (Check-in).
    """
    list_display = ('municipe', 'conta_destino', 'data_checkin', 'registrado_por')
    list_filter = ('data_checkin', 'conta_destino', 'registrado_por')
    search_fields = ('municipe__nome_completo', 'observacao', 'conta_destino__nome')
    readonly_fields = ('data_checkin',) # A data do check-in não deve ser alterada

    # Otimiza as consultas no painel admin
    list_select_related = ('municipe', 'conta_destino', 'registrado_por')

@admin.register(ReservaEspaco)
class ReservaEspacoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'espaco', 'data_inicio', 'data_fim', 'responsavel')
    list_filter = ('espaco', 'responsavel')
    search_fields = ('titulo', 'observacoes')

@admin.register(Lembrete)
class LembreteAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'conta', 'usuario', 'data_criacao', 'data_atualizacao')
    list_filter = ('conta', 'usuario', 'data_criacao')
    search_fields = ('titulo', 'conteudo')
    list_per_page = 20
    
    # Define os campos que serão exibidos no formulário de edição
    fields = ('conta', 'titulo', 'conteudo')

    def save_model(self, request, obj, form, change):
        """
        Ao salvar um lembrete pelo admin, define o usuário logado como o criador,
        caso seja uma nova criação.
        """
        if not obj.pk: # Verifica se é um novo objeto
            obj.usuario = request.user
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        """
        Filtra os lembretes que o usuário pode ver. Superusuários veem todos,
        outros usuários (como Secretárias com acesso ao admin) veem apenas
        os lembretes das suas contas.
        """
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        if hasattr(request.user, 'perfil'):
            return qs.filter(conta__in=request.user.perfil.contas.all())
        return qs.none()