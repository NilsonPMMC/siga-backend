import os
import re
import string
import secrets
from datetime import datetime

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import models
from django.db.models import Count
from django.template.response import TemplateResponse
from django.template.loader import render_to_string
from django.utils import timezone as tz
from django.utils.html import format_html
from django.utils.crypto import get_random_string
from django.core.management import call_command
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from import_export.fields import Field
from import_export.widgets import ForeignKeyWidget, ManyToManyWidget
from .utils import enviar_email_com_cid
from .admin_utils import resumo_categorias_municipes, injetar_resumo_changelist
from .models import (
    Conta,
    AutomacaoAniversarioConta,
    AutomacaoRelatorioDiarioConta,
    CampanhaEmail,
    CampanhaDestinatario,
    CampanhaLogEnvio,
    Municipe,
    PerfilMunicipe,
    Atendimento,
    Tramitacao,
    CategoriaAtendimento,
    AssuntoAtendimento,
    SlaAtendimentoConfig,
    ReservaEspaco,
    SolicitacaoAgenda,
    Anexo,
    LogDeAtividade,
    PerfilUsuario,
    Notificacao,
    CategoriaContato,
    Espaco,
    RegistroVisita,
    Lembrete,
    TramitacaoAgenda,
    SinapseSecretaria,
    # Múltiplas contas Google - Fase 2
    ContaGoogleCalendar,
    TokenGoogleCalendar,
    UsuarioContaGooglePermissao,
    GoogleApiToken,
)
from .services.campanhas_email import prepare_campaign_recipients


class AtendimentoAdminForm(forms.ModelForm):
    """Form com data_criacao como campo explícito (model tem auto_now_add=True, não pode estar em Meta.fields)."""
    data_criacao = forms.DateTimeField(
        label='Data de Criação',
        required=True,
        help_text='Ajuste aqui quando o registro foi inserido com data incorreta (ex.: dados retroativos).',
    )

    class Meta:
        model = Atendimento
        fields = [
            'titulo', 'descricao', 'assunto', 'status', 'conta', 'municipe', 'responsavel', 'created_by',
            'categorias', 'assunto_ia_sugerido', 'assunto_ia_status',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.data_criacao:
            self.fields['data_criacao'].initial = tz.localtime(self.instance.data_criacao)
        elif not (self.instance and self.instance.pk):
            self.fields['data_criacao'].initial = tz.now()

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.data_criacao = self.cleaned_data['data_criacao']
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class DisparoAvancadoForm(forms.Form):
    data_alvo = forms.DateField(
        required=False,
        label="Data alvo",
        help_text="Opcional. Se vazio, usa a data padrão da rotina.",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    email_teste = forms.EmailField(
        required=False,
        label="E-mail de teste",
        help_text="Opcional. Se preenchido, sobrepõe destinatários reais.",
    )
    send = forms.BooleanField(
        required=False,
        initial=False,
        label="Executar envio real",
        help_text="Desmarcado = DRY-RUN (simulação sem envio).",
    )

def enviar_email_de_acesso(modeladmin, request, queryset):
    """
    Ação do Django Admin para gerar nova senha e enviar com imagens (CID).
    """
    cont_enviados = 0
    
    for user in queryset:
        if not user.email:
            messages.warning(request, f"O usuário '{user.username}' não possui e-mail e foi pulado.")
            continue

        try:
            # 1. Gera senha forte (8 caracteres é suficiente e mais fácil de copiar)
            senha_provisoria = get_random_string(length=8)
            
            # 2. Salva a senha
            user.set_password(senha_provisoria)
            user.save()

            # 3. Tenta descobrir a Conta (Secretaria) do usuário para mandar o Logo certo
            # Se o usuário tiver Perfil e estiver vinculado a alguma conta, pegamos a primeira.
            conta_alvo = None
            if hasattr(user, 'perfil') and user.perfil.contas.exists():
                conta_alvo = user.perfil.contas.first()

            caminho_logo = os.path.join(settings.STATIC_ROOT, 'images', 'logo-siga-gab.png')
    
            # Se quiser garantir, converta para URI de arquivo
            if os.path.exists(caminho_logo):
                logo_uri = f"file://{caminho_logo}"
            else:
                # Fallback caso não ache (opcional)
                logo_uri = ""

            # 4. Prepara o contexto
            contexto = {
                'nome_usuario': user.get_full_name() or user.username,
                'username': user.username,
                'senha_provisoria': senha_provisoria,
                'link_sistema': 'https://gabinete.mogidascruzes.sp.gov.br',
                'logo_uri': logo_uri,
            }
            
            # 5. Envia usando o Utilitário (Resolve imagens quebradas)
            enviar_email_com_cid(
                assunto='Suas Credenciais de Acesso ao Sistema SIGA Gabinete',
                destinatarios=[user.email],
                template='emails/envio_credenciais.html',
                contexto=contexto,
                conta=conta_alvo # Se for None, o utils manda o Brasão Padrão
            )
            
            cont_enviados += 1

        except Exception as e:
            messages.error(request, f"Falha ao enviar para '{user.username}': {e}")

    if cont_enviados > 0:
        messages.success(request, f"{cont_enviados} credenciais enviadas com sucesso!")

# Registra a ação no Admin de Usuários
enviar_email_de_acesso.short_description = "Gerar senha e enviar credenciais por e-mail"

# --- Configuração do Perfil de Usuário no Admin ---
class PerfilUsuarioInline(admin.StackedInline):
    model = PerfilUsuario
    can_delete = False
    verbose_name_plural = 'Perfil de Vínculos'
    fk_name = 'usuario'
    filter_horizontal = ('contas', 'categorias_contato')
    fields = ('contas', 'categorias_contato', 'pode_visualizar_agendas_compartilhadas')

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
class ContaGoogleCalendarInline(admin.TabularInline):
    """Inline para gerenciar contas Google diretamente no admin da Conta"""
    model = ContaGoogleCalendar
    extra = 0
    fields = ['nome', 'email_google', 'ativa', 'eh_padrao', 'total_usuarios']
    readonly_fields = ['total_usuarios']
    
    def total_usuarios(self, obj):
        """Quantos usuários têm acesso a esta conta Google"""
        if obj.pk:
            total = obj.permissoes_usuarios.filter(pode_visualizar=True).count()
            return f"{total} usuário(s)"
        return "0 usuário(s)"
    total_usuarios.short_description = 'Usuários'


@admin.register(Conta)
class ContaAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'nome', 'nome_sigla', 'participa_escala', 
        'nome_titular', 'total_contas_google_display'
    ) 
    
    # --- ESTA LINHA É OBRIGATÓRIA PARA O AUTOCOMPLETE FUNCIONAR ---
    search_fields = ['nome', 'nome_sigla', 'nome_titular'] 
    # --------------------------------------------------------------
    
    list_filter = ('participa_escala',)

    fieldsets = (
        (None, {
            'fields': ('nome_instituicao', 'nome', 'nome_sigla', 'nome_titular', 'etiqueta_remetente')
        }),
        ('Configurações de Escala', {
            'fields': ('participa_escala',)
        }),
        ('Dados Ofício', {
            'fields': ('ultimo_numero_oficio', 'ano_corrente_oficio')
        }),
        ('Assinatura Eletrônica', {
            'fields': ('assinatura_eletronica', 'usar_assinatura_eletronica'),
            'description': 'Configure a assinatura eletrônica que será incluída nos ofícios gerados. Faça upload da imagem e marque o checkbox para ativar.'
        }),
        ('Personalização e Integrações', {
            'fields': ('brasao_instituicao', 'logo_conta', 'google_calendar_id_legado_display')
        }),
    )
    
    readonly_fields = ['google_calendar_id_legado_display']
    inlines = [ContaGoogleCalendarInline]
    
    def total_contas_google_display(self, obj):
        """Mostra total de contas Google configuradas"""
        total = obj.total_contas_google
        if total == 0:
            return format_html('<span style="color: red;">❌ Nenhuma</span>')
        elif total == 1:
            return format_html('<span style="color: green;">✅ 1 conta</span>')
        else:
            return format_html('<span style="color: blue;">📊 {} contas</span>', total)
    total_contas_google_display.short_description = 'Contas Google'
    
    def google_calendar_id_legado_display(self, obj):
        """Mostra o campo legado apenas como informação"""
        if obj.google_calendar_id:
            return format_html(
                '<div style="padding: 8px; background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 4px;">'
                '<strong>⚠️ Campo Legado:</strong> {}<br>'
                '<small style="color: #856404;">Use o sistema de "Múltiplas Contas Google" abaixo para gerenciar</small>'
                '</div>',
                obj.google_calendar_id
            )
        return format_html(
            '<span style="color: #6c757d; font-style: italic;">'
            'Nenhum calendar configurado no sistema legado'
            '</span>'
        )
    google_calendar_id_legado_display.short_description = 'Google Calendar (Legado)'


@admin.register(AutomacaoAniversarioConta)
class AutomacaoAniversarioContaAdmin(admin.ModelAdmin):
    list_display = ("conta", "ativo", "from_email", "env_var_smtp_user", "data_atualizacao")
    list_filter = ("ativo", "smtp_use_tls", "smtp_use_ssl")
    search_fields = ("conta__nome", "env_var_smtp_user", "from_email")
    autocomplete_fields = ("conta", "alerta_usuarios")
    actions = (
        "acao_aniversario_dry_run",
        "acao_aniversario_enviar",
        "acao_alerta_gestores_dry_run",
        "acao_alerta_gestores_enviar",
        "acao_aniversario_avancada",
        "acao_alerta_gestores_avancada",
    )
    readonly_fields = (
        "data_criacao",
        "data_atualizacao",
        "mostrar_env_var_smtp_user_esperada",
        "mostrar_env_var_smtp_pass_esperada",
    )
    fieldsets = (
        ("Conta e status", {
            "fields": (
                "conta",
                "ativo",
                "mostrar_env_var_smtp_user_esperada",
                "mostrar_env_var_smtp_pass_esperada",
                "data_criacao",
                "data_atualizacao",
            ),
        }),
        ("SMTP por conta", {
            "fields": (
                "smtp_host",
                "smtp_port",
                "smtp_use_tls",
                "smtp_use_ssl",
                "env_var_smtp_user",
                "env_var_smtp_pass",
                "from_email",
            ),
            "description": (
                "Cadastre os NOMES das variáveis no .env do servidor SIGA "
                "(ex.: SMTP_USER_PREFEITA / SMTP_PASS_PREFEITA)."
            ),
        }),
        ("Template de aniversário", {
            "fields": ("assunto_template", "corpo_template", "arte"),
            "description": (
                "Suporta placeholders Django Template como {{ nome_completo }}, "
                "{{ conta_nome }}, {{ conta_nome_titular }} e {{ data_alvo|date:'d/m/Y' }}."
            ),
        }),
        ("Alerta para gestores (consolidado)", {
            "fields": (
                "alerta_gestores_ativo",
                "alerta_categoria",
                "alerta_usuarios",
                "alerta_assunto_template",
                "alerta_corpo_template",
            ),
            "description": (
                "Configuração do e-mail consolidado para usuários internos. "
                "O relatório de aniversariantes é enviado em anexo (CSV)."
            ),
        }),
    )

    @admin.display(description="Var esperada (SMTP usuário)")
    def mostrar_env_var_smtp_user_esperada(self, obj):
        if not obj:
            return "Defina a conta para ver a variável esperada."
        return obj.env_var_smtp_user_esperada or "Conta sem sigla (preencha nome_sigla)."

    @admin.display(description="Var esperada (SMTP senha)")
    def mostrar_env_var_smtp_pass_esperada(self, obj):
        if not obj:
            return "Defina a conta para ver a variável esperada."
        return obj.env_var_smtp_pass_esperada or "Conta sem sigla (preencha nome_sigla)."

    @admin.action(description="Aniversário (manual) - DRY-RUN nas contas selecionadas")
    def acao_aniversario_dry_run(self, request, queryset):
        sucesso = 0
        falhas = 0
        for automacao in queryset.select_related("conta"):
            try:
                call_command(
                    "enviar_aniversariantes_servidores",
                    conta_id=automacao.conta_id,
                    verbosity=1,
                )
                sucesso += 1
            except Exception as exc:
                falhas += 1
                self.message_user(
                    request,
                    f"[FALHA] Conta {automacao.conta.nome}: {exc}",
                    level=messages.ERROR,
                )
        self.message_user(
            request,
            f"Ação concluída (DRY-RUN aniversário). Sucesso: {sucesso} | Falhas: {falhas}.",
            level=messages.SUCCESS if falhas == 0 else messages.WARNING,
        )

    @admin.action(description="Aniversário (manual) - ENVIAR nas contas selecionadas")
    def acao_aniversario_enviar(self, request, queryset):
        sucesso = 0
        falhas = 0
        for automacao in queryset.select_related("conta"):
            try:
                call_command(
                    "enviar_aniversariantes_servidores",
                    conta_id=automacao.conta_id,
                    send=True,
                    verbosity=1,
                )
                sucesso += 1
            except Exception as exc:
                falhas += 1
                self.message_user(
                    request,
                    f"[FALHA] Conta {automacao.conta.nome}: {exc}",
                    level=messages.ERROR,
                )
        self.message_user(
            request,
            f"Ação concluída (ENVIO aniversário). Sucesso: {sucesso} | Falhas: {falhas}.",
            level=messages.SUCCESS if falhas == 0 else messages.WARNING,
        )

    @admin.action(description="Alerta gestores (manual) - DRY-RUN nas contas selecionadas")
    def acao_alerta_gestores_dry_run(self, request, queryset):
        sucesso = 0
        falhas = 0
        for automacao in queryset.select_related("conta"):
            try:
                call_command(
                    "enviar_alerta_aniversarios_gestores",
                    conta_id=automacao.conta_id,
                    verbosity=1,
                )
                sucesso += 1
            except Exception as exc:
                falhas += 1
                self.message_user(
                    request,
                    f"[FALHA] Conta {automacao.conta.nome}: {exc}",
                    level=messages.ERROR,
                )
        self.message_user(
            request,
            f"Ação concluída (DRY-RUN alerta gestores). Sucesso: {sucesso} | Falhas: {falhas}.",
            level=messages.SUCCESS if falhas == 0 else messages.WARNING,
        )

    @admin.action(description="Alerta gestores (manual) - ENVIAR nas contas selecionadas")
    def acao_alerta_gestores_enviar(self, request, queryset):
        sucesso = 0
        falhas = 0
        for automacao in queryset.select_related("conta"):
            try:
                call_command(
                    "enviar_alerta_aniversarios_gestores",
                    conta_id=automacao.conta_id,
                    send=True,
                    verbosity=1,
                )
                sucesso += 1
            except Exception as exc:
                falhas += 1
                self.message_user(
                    request,
                    f"[FALHA] Conta {automacao.conta.nome}: {exc}",
                    level=messages.ERROR,
                )
        self.message_user(
            request,
            f"Ação concluída (ENVIO alerta gestores). Sucesso: {sucesso} | Falhas: {falhas}.",
            level=messages.SUCCESS if falhas == 0 else messages.WARNING,
        )

    def _render_disparo_avancado_form(self, request, queryset, action_name, titulo):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        form = DisparoAvancadoForm()
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "titulo": titulo,
            "queryset": queryset,
            "form": form,
            "action_name": action_name,
            "selected_ids": selected,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
        }
        return TemplateResponse(
            request,
            "admin/atendimentos/automacaoaniversarioconta/disparo_avancado_form.html",
            context,
        )

    @admin.action(description="Aniversário (manual) - AVANÇADO (data/e-mail teste)")
    def acao_aniversario_avancada(self, request, queryset):
        if "apply" not in request.POST:
            return self._render_disparo_avancado_form(
                request,
                queryset,
                "acao_aniversario_avancada",
                "Disparo avançado - Aniversário",
            )

        form = DisparoAvancadoForm(request.POST)
        if not form.is_valid():
            selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
            context = {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "titulo": "Disparo avançado - Aniversário",
                "queryset": queryset,
                "form": form,
                "action_name": "acao_aniversario_avancada",
                "selected_ids": selected,
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
            }
            return TemplateResponse(
                request,
                "admin/atendimentos/automacaoaniversarioconta/disparo_avancado_form.html",
                context,
            )

        ids = request.POST.getlist(ACTION_CHECKBOX_NAME)
        automacoes = self.model.objects.filter(pk__in=ids).select_related("conta")
        sucesso = 0
        falhas = 0
        for automacao in automacoes:
            try:
                kwargs = {"conta_id": automacao.conta_id, "verbosity": 1}
                if form.cleaned_data["data_alvo"]:
                    kwargs["data_alvo"] = form.cleaned_data["data_alvo"].strftime("%Y-%m-%d")
                if form.cleaned_data["email_teste"]:
                    kwargs["email_teste"] = form.cleaned_data["email_teste"]
                if form.cleaned_data["send"]:
                    kwargs["send"] = True
                call_command("enviar_aniversariantes_servidores", **kwargs)
                sucesso += 1
            except Exception as exc:
                falhas += 1
                self.message_user(request, f"[FALHA] Conta {automacao.conta.nome}: {exc}", level=messages.ERROR)

        modo = "ENVIO" if form.cleaned_data["send"] else "DRY-RUN"
        self.message_user(
            request,
            f"Ação avançada concluída ({modo} aniversário). Sucesso: {sucesso} | Falhas: {falhas}.",
            level=messages.SUCCESS if falhas == 0 else messages.WARNING,
        )

    @admin.action(description="Alerta gestores (manual) - AVANÇADO (data/e-mail teste)")
    def acao_alerta_gestores_avancada(self, request, queryset):
        if "apply" not in request.POST:
            return self._render_disparo_avancado_form(
                request,
                queryset,
                "acao_alerta_gestores_avancada",
                "Disparo avançado - Alerta de gestores",
            )

        form = DisparoAvancadoForm(request.POST)
        if not form.is_valid():
            selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
            context = {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "titulo": "Disparo avançado - Alerta de gestores",
                "queryset": queryset,
                "form": form,
                "action_name": "acao_alerta_gestores_avancada",
                "selected_ids": selected,
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
            }
            return TemplateResponse(
                request,
                "admin/atendimentos/automacaoaniversarioconta/disparo_avancado_form.html",
                context,
            )

        ids = request.POST.getlist(ACTION_CHECKBOX_NAME)
        automacoes = self.model.objects.filter(pk__in=ids).select_related("conta")
        sucesso = 0
        falhas = 0
        for automacao in automacoes:
            try:
                kwargs = {"conta_id": automacao.conta_id, "verbosity": 1}
                if form.cleaned_data["data_alvo"]:
                    kwargs["data_anterior"] = form.cleaned_data["data_alvo"].strftime("%Y-%m-%d")
                if form.cleaned_data["email_teste"]:
                    kwargs["email_teste"] = form.cleaned_data["email_teste"]
                if form.cleaned_data["send"]:
                    kwargs["send"] = True
                call_command("enviar_alerta_aniversarios_gestores", **kwargs)
                sucesso += 1
            except Exception as exc:
                falhas += 1
                self.message_user(request, f"[FALHA] Conta {automacao.conta.nome}: {exc}", level=messages.ERROR)

        modo = "ENVIO" if form.cleaned_data["send"] else "DRY-RUN"
        self.message_user(
            request,
            f"Ação avançada concluída ({modo} alerta gestores). Sucesso: {sucesso} | Falhas: {falhas}.",
            level=messages.SUCCESS if falhas == 0 else messages.WARNING,
        )


@admin.register(AutomacaoRelatorioDiarioConta)
class AutomacaoRelatorioDiarioContaAdmin(admin.ModelAdmin):
    list_display = ("conta", "ativo", "dias_offset", "from_email", "env_var_smtp_user", "data_atualizacao")
    list_filter = ("ativo", "enviar_relatorio_atendimentos", "smtp_use_tls")
    search_fields = ("conta__nome", "env_var_smtp_user", "from_email")
    autocomplete_fields = ("conta", "destinatarios")
    actions = (
        "acao_relatorios_dry_run",
        "acao_relatorios_enviar",
        "acao_relatorios_avancada",
    )
    readonly_fields = (
        "data_criacao",
        "data_atualizacao",
        "mostrar_env_var_smtp_user_esperada",
        "mostrar_env_var_smtp_pass_esperada",
    )
    fieldsets = (
        ("Conta e status", {
            "fields": (
                "conta",
                "ativo",
                "dias_offset",
                "mostrar_env_var_smtp_user_esperada",
                "mostrar_env_var_smtp_pass_esperada",
                "data_criacao",
                "data_atualizacao",
            ),
        }),
        ("SMTP por conta", {
            "fields": (
                "smtp_host",
                "smtp_port",
                "smtp_use_tls",
                "smtp_use_ssl",
                "env_var_smtp_user",
                "env_var_smtp_pass",
                "from_email",
            ),
        }),
        ("Relatórios e destinatários", {
            "fields": (
                "enviar_relatorio_atendimentos",
                "destinatarios",
                "assunto_template",
                "corpo_template",
            ),
            "description": (
                "Envia o PDF equivalente à rota "
                "/api/relatorios/atendimentos/pdf/ "
                "com data_inicio = data_fim = data de referência."
            ),
        }),
    )

    @admin.display(description="Var esperada (SMTP usuário)")
    def mostrar_env_var_smtp_user_esperada(self, obj):
        if not obj:
            return "Defina a conta para ver a variável esperada."
        return obj.env_var_smtp_user_esperada or "Conta sem sigla (preencha nome_sigla)."

    @admin.display(description="Var esperada (SMTP senha)")
    def mostrar_env_var_smtp_pass_esperada(self, obj):
        if not obj:
            return "Defina a conta para ver a variável esperada."
        return obj.env_var_smtp_pass_esperada or "Conta sem sigla (preencha nome_sigla)."

    @admin.action(description="Relatórios diários (manual) - DRY-RUN nas contas selecionadas")
    def acao_relatorios_dry_run(self, request, queryset):
        sucesso = 0
        falhas = 0
        for automacao in queryset.select_related("conta"):
            try:
                call_command(
                    "enviar_relatorios_diarios_gestores",
                    conta_id=automacao.conta_id,
                    verbosity=1,
                )
                sucesso += 1
            except Exception as exc:
                falhas += 1
                self.message_user(
                    request,
                    f"[FALHA] Conta {automacao.conta.nome}: {exc}",
                    level=messages.ERROR,
                )
        self.message_user(
            request,
            f"Ação concluída (DRY-RUN relatórios). Sucesso: {sucesso} | Falhas: {falhas}.",
            level=messages.SUCCESS if falhas == 0 else messages.WARNING,
        )

    @admin.action(description="Relatórios diários (manual) - ENVIAR nas contas selecionadas")
    def acao_relatorios_enviar(self, request, queryset):
        sucesso = 0
        falhas = 0
        for automacao in queryset.select_related("conta"):
            try:
                call_command(
                    "enviar_relatorios_diarios_gestores",
                    conta_id=automacao.conta_id,
                    send=True,
                    verbosity=1,
                )
                sucesso += 1
            except Exception as exc:
                falhas += 1
                self.message_user(
                    request,
                    f"[FALHA] Conta {automacao.conta.nome}: {exc}",
                    level=messages.ERROR,
                )
        self.message_user(
            request,
            f"Ação concluída (ENVIO relatórios). Sucesso: {sucesso} | Falhas: {falhas}.",
            level=messages.SUCCESS if falhas == 0 else messages.WARNING,
        )

    def _render_disparo_avancado_form(self, request, queryset, action_name, titulo):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        form = DisparoAvancadoForm()
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "titulo": titulo,
            "queryset": queryset,
            "form": form,
            "action_name": action_name,
            "selected_ids": selected,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "cancel_url": "admin:atendimentos_automacaorelatoriodiarioconta_changelist",
        }
        return TemplateResponse(
            request,
            "admin/atendimentos/automacaorelatoriodiarioconta/disparo_avancado_form.html",
            context,
        )

    @admin.action(description="Relatórios diários (manual) - AVANÇADO (data/e-mail teste)")
    def acao_relatorios_avancada(self, request, queryset):
        if "apply" not in request.POST:
            return self._render_disparo_avancado_form(
                request,
                queryset,
                "acao_relatorios_avancada",
                "Disparo avançado - Relatórios diários",
            )

        form = DisparoAvancadoForm(request.POST)
        if not form.is_valid():
            selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
            context = {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "titulo": "Disparo avançado - Relatórios diários",
                "queryset": queryset,
                "form": form,
                "action_name": "acao_relatorios_avancada",
                "selected_ids": selected,
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
                "cancel_url": "admin:atendimentos_automacaorelatoriodiarioconta_changelist",
            }
            return TemplateResponse(
                request,
                "admin/atendimentos/automacaorelatoriodiarioconta/disparo_avancado_form.html",
                context,
            )

        ids = request.POST.getlist(ACTION_CHECKBOX_NAME)
        automacoes = self.model.objects.filter(pk__in=ids).select_related("conta")
        sucesso = 0
        falhas = 0
        for automacao in automacoes:
            try:
                kwargs = {"conta_id": automacao.conta_id, "verbosity": 1}
                if form.cleaned_data["data_alvo"]:
                    kwargs["data_referencia"] = form.cleaned_data["data_alvo"].strftime("%Y-%m-%d")
                if form.cleaned_data["email_teste"]:
                    kwargs["email_teste"] = form.cleaned_data["email_teste"]
                if form.cleaned_data["send"]:
                    kwargs["send"] = True
                call_command("enviar_relatorios_diarios_gestores", **kwargs)
                sucesso += 1
            except Exception as exc:
                falhas += 1
                self.message_user(request, f"[FALHA] Conta {automacao.conta.nome}: {exc}", level=messages.ERROR)

        modo = "ENVIO" if form.cleaned_data["send"] else "DRY-RUN"
        self.message_user(
            request,
            f"Ação avançada concluída ({modo} relatórios). Sucesso: {sucesso} | Falhas: {falhas}.",
            level=messages.SUCCESS if falhas == 0 else messages.WARNING,
        )


class CampanhaDestinatarioInline(admin.TabularInline):
    model = CampanhaDestinatario
    extra = 0
    can_delete = False
    fields = ("municipe", "conta", "categoria", "email_destino", "criado_em")
    readonly_fields = fields
    autocomplete_fields = ("municipe", "conta", "categoria")


@admin.register(CampanhaEmail)
class CampanhaEmailAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "conta",
        "tipo_disparo",
        "status",
        "data_hora_disparo",
        "total_destinatarios",
        "total_enviados",
        "total_falhas",
        "data_criacao",
    )
    list_filter = ("status", "tipo_disparo", "conta", "data_criacao")
    search_fields = ("nome", "conta__nome", "assunto_template")
    autocomplete_fields = ("conta", "categorias", "criado_por")
    filter_horizontal = ("categorias",)
    readonly_fields = (
        "total_destinatarios",
        "total_enviados",
        "total_falhas",
        "disparo_solicitado_em",
        "ultima_execucao_em",
        "data_criacao",
        "data_atualizacao",
    )
    inlines = (CampanhaDestinatarioInline,)
    actions = (
        "acao_preparar_destinatarios",
        "acao_solicitar_disparo_imediato",
        "acao_cancelar_campanha",
    )

    fieldsets = (
        ("Dados principais", {
            "fields": (
                "conta",
                "nome",
                "status",
                "tipo_disparo",
                "data_hora_disparo",
                "categorias",
                "email_teste",
            ),
        }),
        ("Conteúdo", {
            "fields": ("assunto_template", "corpo_template", "arte", "anexo"),
        }),
        ("Auditoria", {
            "fields": (
                "criado_por",
                "disparo_solicitado_em",
                "ultima_execucao_em",
                "total_destinatarios",
                "total_enviados",
                "total_falhas",
                "data_criacao",
                "data_atualizacao",
            ),
        }),
    )

    def save_model(self, request, obj, form, change):
        if not change and not obj.criado_por_id:
            obj.criado_por = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Preparar destinatários (dry-run)")
    def acao_preparar_destinatarios(self, request, queryset):
        ok = 0
        fail = 0
        for campanha in queryset:
            try:
                result = prepare_campaign_recipients(campanha)
                self.message_user(
                    request,
                    f"{campanha.nome}: destinatários={result['criados']} sem_email={result['sem_email']}",
                    level=messages.INFO,
                )
                ok += 1
            except Exception as exc:
                fail += 1
                self.message_user(
                    request,
                    f"[FALHA] {campanha.nome}: {exc}",
                    level=messages.ERROR,
                )
        self.message_user(
            request,
            f"Ação concluída (preparar destinatários). Sucesso: {ok} | Falhas: {fail}.",
            level=messages.SUCCESS if fail == 0 else messages.WARNING,
        )

    @admin.action(description="Solicitar disparo imediato (fila)")
    def acao_solicitar_disparo_imediato(self, request, queryset):
        agora = tz.now()
        atualizados = 0
        for campanha in queryset:
            if campanha.status in ("CONCLUIDA", "PROCESSANDO", "CANCELADA"):
                continue
            campanha.tipo_disparo = "IMEDIATO"
            campanha.data_hora_disparo = agora
            campanha.disparo_solicitado_em = agora
            campanha.status = "AGENDADA"
            campanha.save(
                update_fields=[
                    "tipo_disparo",
                    "data_hora_disparo",
                    "disparo_solicitado_em",
                    "status",
                    "data_atualizacao",
                ]
            )
            atualizados += 1

        self.message_user(
            request,
            f"{atualizados} campanha(s) marcada(s) para processamento por comando/cron.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Cancelar campanha")
    def acao_cancelar_campanha(self, request, queryset):
        total = queryset.exclude(status="PROCESSANDO").update(status="CANCELADA")
        self.message_user(
            request,
            f"{total} campanha(s) cancelada(s).",
            level=messages.WARNING,
        )


@admin.register(CampanhaDestinatario)
class CampanhaDestinatarioAdmin(admin.ModelAdmin):
    list_display = ("campanha", "municipe", "email_destino", "conta", "categoria", "criado_em")
    list_filter = ("conta", "categoria", "criado_em")
    search_fields = ("campanha__nome", "municipe__nome_completo", "email_destino")
    autocomplete_fields = ("campanha", "municipe", "conta", "categoria")
    readonly_fields = ("criado_em",)


@admin.register(CampanhaLogEnvio)
class CampanhaLogEnvioAdmin(admin.ModelAdmin):
    list_display = ("campanha", "destinatario", "status", "data_envio")
    list_filter = ("status", "data_envio", "campanha")
    search_fields = ("campanha__nome", "destinatario__municipe__nome_completo", "erro")
    autocomplete_fields = ("campanha", "destinatario")
    readonly_fields = ("data_envio",)


# --- Filtros auxiliares para IA (vetores) ---
class TemVetorPerfilIAFilter(admin.SimpleListFilter):
    title = "Vetor IA Perfil"
    parameter_name = "vetor_ia_perfil"

    def lookups(self, request, model_admin):
        return (
            ("sim", "Com vetor IA"),
            ("nao", "Sem vetor IA"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "sim":
            return queryset.exclude(vetor_ia_perfil__isnull=True).exclude(vetor_ia_perfil=[])
        if value == "nao":
            return queryset.filter(
                models.Q(vetor_ia_perfil__isnull=True) | models.Q(vetor_ia_perfil=[])
            )
        return queryset


class TemVetorAtendimentoIAFilter(admin.SimpleListFilter):
    title = "Vetor IA Atendimento"
    parameter_name = "vetor_ia_atendimento"

    def lookups(self, request, model_admin):
        return (
            ("sim", "Com vetor IA"),
            ("nao", "Sem vetor IA"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "sim":
            return queryset.exclude(vetor_ia_atendimento__isnull=True).exclude(
                vetor_ia_atendimento=[]
            )
        if value == "nao":
            return queryset.filter(
                models.Q(vetor_ia_atendimento__isnull=True)
                | models.Q(vetor_ia_atendimento=[])
            )
        return queryset


# --- Configuração de Importação/Exportação para Munícipe ---
class MunicipeResource(resources.ModelResource):
    # --- MAPEAMENTO DOS CAMPOS (categoria está em PerfilMunicipe) ---
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
        fields = ('id', 'nome_completo', 'cpf', 'data_nascimento', 'emails', 'telefones', 'cargo', 'orgao', 'contas')
        export_order = fields


# --- Admin para o modelo Munícipe ---
@admin.register(Municipe)
class MunicipeAdmin(ImportExportModelAdmin):
    resource_class = MunicipeResource
    change_list_template = 'admin/atendimentos/municipe/change_list.html'
    list_display = (
        "nome_completo",
        "tratamento",
        "cpf",
        "get_email_principal",
        "get_telefone_principal",
        "get_categorias",
        "listar_contas",
        "tem_perfis_duplicados_admin",
        "tem_vetor_ia",
        "auditoria_ia_data",
    )
    search_fields = ("nome_completo", "cpf", "emails__email", "perfil_ia_texto")
    list_filter = ("perfis__categoria", "contas", TemVetorPerfilIAFilter, "auditoria_ia_data")
    filter_horizontal = ("contas",)

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        try:
            cl = response.context_data['cl']
            resumo = resumo_categorias_municipes(cl.queryset)
        except (AttributeError, KeyError, TypeError):
            resumo = []
        return injetar_resumo_changelist(response, 'resumo_categorias', resumo)

    def get_telefone_principal(self, obj):
        if obj.telefones and len(obj.telefones) > 0:
            return obj.telefones[0].get("numero")
        return "N/A"

    get_telefone_principal.short_description = "Telefone"

    def get_email_principal(self, obj):
        if obj.emails and len(obj.emails) > 0:
            return obj.emails[0].get("email")
        return "N/A"

    get_email_principal.short_description = "Email Principal"

    def get_categorias(self, obj):
        cats = [
            p.categoria.nome
            for p in obj.perfis.select_related("categoria")
            if p.categoria
        ]
        return ", ".join(sorted(set(cats))) if cats else "-"

    get_categorias.short_description = "Categorias"

    def listar_contas(self, obj):
        return ", ".join([conta.nome for conta in obj.contas.all()])

    listar_contas.short_description = "Contas"

    def tem_perfis_duplicados_admin(self, obj):
        from atendimentos.services.perfil_municipe import municipe_tem_perfis_duplicados

        return municipe_tem_perfis_duplicados(obj.perfis.all())

    tem_perfis_duplicados_admin.boolean = True
    tem_perfis_duplicados_admin.short_description = "Perfis dup.?"

    def tem_vetor_ia(self, obj):
        return bool(obj.vetor_ia_perfil)

    tem_vetor_ia.boolean = True
    tem_vetor_ia.short_description = "Vetor IA?"


@admin.register(PerfilMunicipe)
class PerfilMunicipeAdmin(admin.ModelAdmin):
    list_display = ('municipe', 'conta', 'cargo', 'instituicao', 'ativo')
    list_filter = ('conta', 'ativo')
    search_fields = ('municipe__nome_completo', 'cargo', 'instituicao', 'conta__nome')
    autocomplete_fields = ('municipe', 'conta')
    actions = ('detectar_duplicados_cargo_conta',)

    @admin.action(description="Detectar duplicados (mesmo munícipe + conta + cargo)")
    def detectar_duplicados_cargo_conta(self, request, queryset):
        from atendimentos.services.perfil_municipe import municipe_tem_perfis_duplicados

        municipes_ids = queryset.values_list('municipe_id', flat=True).distinct()
        total = 0
        for municipe_id in municipes_ids:
            perfis = list(PerfilMunicipe.objects.filter(municipe_id=municipe_id).only('conta_id', 'cargo'))
            if municipe_tem_perfis_duplicados(perfis):
                total += 1
        self.message_user(
            request,
            f"{total} munícipe(s) com perfis duplicados (cargo+conta) entre os selecionados. "
            f"Use: python manage.py limpar_perfis_municipe_duplicados --apply",
            level=messages.WARNING if total else messages.SUCCESS,
        )


# --- Admin para outros modelos ---
@admin.register(AssuntoAtendimento)
class AssuntoAtendimentoAdmin(admin.ModelAdmin):
    list_display = ("nome", "codigo", "ordem", "ativo")
    list_filter = ("ativo",)
    list_editable = ("ordem", "ativo")
    search_fields = ("nome", "codigo", "descricao")
    ordering = ("ordem", "nome")
    fieldsets = (
        (None, {"fields": ("nome", "codigo", "descricao", "ordem", "ativo")}),
    )


@admin.register(SlaAtendimentoConfig)
class SlaAtendimentoConfigAdmin(admin.ModelAdmin):
    list_display = ('conta', 'assunto', 'dias_resposta', 'dias_conclusao', 'ativo')
    list_filter = ('conta', 'ativo', 'assunto')
    search_fields = ('conta__nome', 'assunto__nome')
    autocomplete_fields = ('conta', 'assunto')


@admin.register(Atendimento)
class AtendimentoAdmin(admin.ModelAdmin):
    form = AtendimentoAdminForm
    list_display = (
        "protocolo",
        "titulo",
        "assunto",
        "municipe",
        "conta",
        "status",
        "assunto_ia_status",
        "auditoria_ia_status",
        "tem_vetor_ia",
        "data_criacao",
    )
    list_filter = (
        "status",
        "conta",
        "assunto",
        "assunto_ia_status",
        "data_criacao",
        "categorias",
        "auditoria_ia_status",
        TemVetorAtendimentoIAFilter,
    )
    search_fields = ("protocolo", "titulo", "municipe__nome_completo", "assunto__nome", "resumo_ia_local")
    filter_horizontal = ("categorias",)
    autocomplete_fields = ("assunto", "assunto_ia_sugerido", "municipe", "conta", "responsavel", "created_by")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "protocolo",
                    "titulo",
                    "descricao",
                    "assunto",
                    "status",
                    "conta",
                    "municipe",
                    "responsavel",
                    "created_by",
                    "categorias",
                )
            },
        ),
        (
            "Classificação por IA (assunto)",
            {
                "fields": ("assunto_ia_sugerido", "assunto_ia_status"),
                "classes": ("collapse",),
            },
        ),
        (
            "Datas (ajuste de registro)",
            {
                "fields": ("data_criacao", "data_atualizacao"),
                "description": 'Use "Data de Criação" para corrigir a data de registro quando o atendimento foi inserido com data incorreta (ex.: dados retroativos).',
            },
        ),
        (
            "Inteligência Artificial",
            {
                "fields": (
                    "auditoria_ia_status",
                    "resumo_ia_local",
                    "vetor_ia_atendimento",
                ),
                "classes": ("collapse",),
            },
        ),
    )
    readonly_fields = (
        "protocolo",
        "data_atualizacao",
        "auditoria_ia_status",
        "resumo_ia_local",
        "vetor_ia_atendimento",
    )

    def get_form(self, request, obj=None, **kwargs):
        """Excluir do model form os campos não editáveis; data_criacao vem do form explícito; protocolo/data_atualizacao em readonly."""
        exclude = list(kwargs.get('exclude', []))
        for f in ('protocolo', 'data_criacao', 'data_atualizacao'):
            if f not in exclude:
                exclude.append(f)
        kwargs['exclude'] = exclude
        return super().get_form(request, obj=obj, **kwargs)

    def tem_vetor_ia(self, obj):
        return bool(obj.vetor_ia_atendimento)

    tem_vetor_ia.boolean = True
    tem_vetor_ia.short_description = "Vetor IA?"
    
@admin.register(Tramitacao)
class TramitacaoAdmin(admin.ModelAdmin):
    list_display = ('atendimento', 'usuario', 'data_tramitacao', 'status_anterior', 'status_novo', 'alterou_status')
    list_filter = ('alterou_status', 'status_novo', 'data_tramitacao')
    search_fields = ('atendimento__protocolo', 'atendimento__titulo', 'despacho', 'usuario__username')
    readonly_fields = ('data_tramitacao',)
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('atendimento', 'usuario', 'data_tramitacao', 'despacho')
        }),
        ('Mudança de Status', {
            'fields': ('alterou_status', 'status_anterior', 'status_novo')
        }),
        ('Encaminhamento', {
            'fields': ('encaminhado_para_sinapse_id', 'encaminhado_para_nome', 'encaminhado_para_tipo'),
            'classes': ('collapse',)
        }),
    )

@admin.register(LogDeAtividade)
class LogDeAtividadeAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'usuario', 'acao', 'conta', 'entidade_resumo', 'detalhes_resumo')
    list_filter = ('acao', 'conta', 'timestamp', 'content_type')
    search_fields = ('detalhes', 'usuario__username', 'payload')
    readonly_fields = (
        'timestamp', 'usuario', 'acao', 'detalhes', 'conta', 'payload',
        'content_type', 'object_id',
    )
    date_hierarchy = 'timestamp'
    list_select_related = ('usuario', 'conta', 'content_type')

    @admin.display(description='Entidade')
    def entidade_resumo(self, obj):
        if obj.content_type:
            return f"{obj.content_type.model} #{obj.object_id}"
        return '—'

    @admin.display(description='Detalhes')
    def detalhes_resumo(self, obj):
        if len(obj.detalhes) > 80:
            return f"{obj.detalhes[:80]}…"
        return obj.detalhes

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

admin.site.register(Anexo)
admin.site.register(CategoriaAtendimento)

@admin.register(SinapseSecretaria)
class SinapseSecretariaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'sigla', 'tipo', 'ativo', 'data_atualizacao')
    list_filter = ('ativo', 'tipo', 'data_atualizacao')
    search_fields = ('nome', 'sigla', 'sinapse_id')
    readonly_fields = ('data_atualizacao',)

@admin.register(CategoriaContato)
class CategoriaContatoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativa', 'total_municipes')
    list_filter = ('ativa',)
    search_fields = ('nome',)
    ordering = ('nome',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _total_municipes=Count('perfis_municipe__municipe', distinct=True),
        )

    @admin.display(description='Munícipes', ordering='_total_municipes')
    def total_municipes(self, obj):
        return getattr(obj, '_total_municipes', 0)

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
    list_display = ('municipe', 'conta_destino', 'data_checkin', 'atendimento', 'registrado_por')
    list_filter = ('data_checkin', 'conta_destino', 'registrado_por')
    search_fields = ('municipe__nome_completo', 'observacao', 'conta_destino__nome', 'atendimento__protocolo')
    readonly_fields = ('data_checkin',)
    autocomplete_fields = ('municipe', 'conta_destino', 'usuario_destino', 'registrado_por', 'atendimento')
    list_select_related = ('municipe', 'conta_destino', 'registrado_por', 'atendimento')

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

class TramitacaoAgendaInline(admin.TabularInline):
    model = TramitacaoAgenda
    fields = ('despacho', 'usuario', 'data_tramitacao')
    readonly_fields = ('usuario', 'data_tramitacao',)
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

@admin.register(SolicitacaoAgenda)
class SolicitacaoAgendaAdmin(ImportExportModelAdmin):
    list_display = ('assunto', 'solicitante', 'conta', 'status', 'data_agendada', 'data_criacao')
    list_filter = ('status', 'conta', 'data_criacao')
    search_fields = ('assunto', 'solicitante__nome_completo', 'conta__nome')
    autocomplete_fields = ['solicitante', 'conta', 'responsavel_analise', 'espaco']

    inlines = [TramitacaoAgendaInline]


# ========================================
# ADMINS MÚLTIPLAS CONTAS GOOGLE - FASE 2
# ========================================

@admin.register(ContaGoogleCalendar)
class ContaGoogleCalendarAdmin(admin.ModelAdmin):
    """
    Admin para gestão de múltiplas contas Google Calendar por gabinete.
    """
    list_display = [
        'conta', 'nome', 'email_google', 'ativa', 'eh_padrao', 
        'total_usuarios_com_acesso', 'usar_credenciais_globais'
    ]
    list_filter = ['conta', 'ativa', 'eh_padrao', 'usar_credenciais_globais']
    search_fields = ['nome', 'email_google', 'conta__nome', 'descricao']
    ordering = ['conta__nome', 'nome']
    autocomplete_fields = ['conta']
    
    fieldsets = (
        ('Identificação', {
            'fields': ('conta', 'nome', 'descricao'),
            'description': 'Configure o nome e descrição desta conta Google'
        }),
        ('Configuração Google', {
            'fields': ('email_google', 'calendar_id'),
            'description': 'Email da conta Google e ID do calendar (normalmente iguais)'
        }),
        ('Credenciais OAuth', {
            'fields': ('usar_credenciais_globais', 'client_id', 'client_secret'),
            'classes': ('collapse',),
            'description': (
                'Por padrão, usa credenciais globais do arquivo .env. '
                'Desmarque apenas se esta conta precisa de credenciais específicas.'
            )
        }),
        ('Status e Configurações', {
            'fields': ('ativa', 'eh_padrao'),
            'description': 'Configurações de ativação e definição de conta padrão'
        })
    )
    
    readonly_fields = ['data_criacao', 'data_atualizacao']
    
    def total_usuarios_com_acesso(self, obj):
        """Mostra quantos usuários têm acesso a esta conta"""
        total = obj.permissoes_usuarios.filter(pode_visualizar=True).count()
        if total == 0:
            return format_html('<span style="color: red;">❌ 0</span>')
        elif total <= 5:
            return format_html('<span style="color: orange;">⚠️ {}</span>', total)
        else:
            return format_html('<span style="color: green;">✅ {}</span>', total)
    total_usuarios_com_acesso.short_description = 'Usuários com Acesso'
    
    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if obj and obj.pk:  # Editando objeto existente
            readonly.extend(['data_criacao', 'data_atualizacao'])
        return readonly
    
    actions = ['criar_permissoes_padrao', 'ativar_contas', 'desativar_contas']
    
    def criar_permissoes_padrao(self, request, queryset):
        """Action para criar permissões padrão para usuários da conta"""
        total_permissoes = 0
        
        for conta_google in queryset:
            # Busca usuários que já têm algum tipo de acesso ao gabinete
            # (adaptar baseado na lógica do sistema)
            usuarios_gabinete = self._get_usuarios_do_gabinete(conta_google.conta)
            
            for usuario in usuarios_gabinete:
                permissao, created = UsuarioContaGooglePermissao.objects.get_or_create(
                    usuario=usuario,
                    conta_google=conta_google,
                    defaults={
                        'pode_visualizar': True,
                        'pode_criar': False,  # Conservador por padrão
                        'pode_editar': False,
                        'pode_excluir': False,
                        'criado_por': request.user,
                    }
                )
                if created:
                    total_permissoes += 1
        
        self.message_user(
            request, 
            f'{total_permissoes} permissões criadas com sucesso!',
            messages.SUCCESS
        )
    criar_permissoes_padrao.short_description = 'Criar permissões padrão (visualização)'
    
    def ativar_contas(self, request, queryset):
        """Ativa contas Google selecionadas"""
        total = queryset.update(ativa=True)
        self.message_user(
            request,
            f'{total} conta(s) Google ativada(s) com sucesso!',
            messages.SUCCESS
        )
    ativar_contas.short_description = 'Ativar contas selecionadas'
    
    def desativar_contas(self, request, queryset):
        """Desativa contas Google selecionadas"""
        total = queryset.update(ativa=False)
        self.message_user(
            request,
            f'{total} conta(s) Google desativada(s)!',
            messages.WARNING
        )
    desativar_contas.short_description = 'Desativar contas selecionadas'
    
    def _get_usuarios_do_gabinete(self, conta):
        """
        Retorna usuários que têm acesso ao gabinete.
        🚨 ADAPTAR baseado na lógica do seu sistema!
        """
        # Abordagem 1: Se há um sistema de perfis
        if hasattr(User, 'profile'):
            return User.objects.filter(
                is_active=True,
                profile__conta=conta
            )
        
        # Abordagem 2: Baseado em grupos
        grupo_conta = conta.nome.upper()
        return User.objects.filter(
            is_active=True,
            groups__name__icontains=grupo_conta
        )
        
        # Fallback: Todos os usuários ativos (para teste)
        return User.objects.filter(is_active=True)


class UsuarioContaGooglePermissaoInline(admin.TabularInline):
    """Inline para gerenciar permissões diretamente no admin de ContaGoogleCalendar"""
    model = UsuarioContaGooglePermissao
    extra = 0
    fields = [
        'usuario', 'pode_visualizar', 'pode_criar', 
        'pode_editar', 'pode_excluir', 'nivel_acesso_display'
    ]
    readonly_fields = ['nivel_acesso_display']
    autocomplete_fields = ['usuario']
    
    def nivel_acesso_display(self, obj):
        """Mostra nível de acesso resumido"""
        if obj.pk:
            return obj.nivel_acesso
        return "-"
    nivel_acesso_display.short_description = 'Nível'


@admin.register(UsuarioContaGooglePermissao)
class UsuarioContaGooglePermissaoAdmin(admin.ModelAdmin):
    """
    Admin para gestão granular de permissões de usuários às contas Google.
    """
    list_display = [
        'usuario_nome', 'conta_google', 'nivel_acesso', 
        'pode_visualizar', 'pode_criar', 'pode_editar', 'pode_excluir',
        'status_token', 'data_criacao'
    ]
    list_filter = [
        'conta_google__conta', 'pode_visualizar', 'pode_criar', 
        'pode_editar', 'pode_excluir', 'data_criacao'
    ]
    search_fields = [
        'usuario__username', 'usuario__first_name', 'usuario__last_name', 
        'conta_google__nome', 'conta_google__email_google'
    ]
    autocomplete_fields = ['usuario', 'conta_google', 'criado_por']
    ordering = ['conta_google__conta__nome', 'conta_google__nome', 'usuario__username']
    
    fieldsets = (
        ('Associação', {
            'fields': ('usuario', 'conta_google', 'criado_por')
        }),
        ('Permissões', {
            'fields': (
                'pode_visualizar', 'pode_criar', 
                'pode_editar', 'pode_excluir'
            ),
            'description': (
                'Configure as permissões específicas deste usuário para esta conta Google. '
                'Visualizar é obrigatório para qualquer outra permissão.'
            )
        })
    )
    
    readonly_fields = ['data_criacao']
    
    def usuario_nome(self, obj):
        """Mostra nome completo do usuário se disponível"""
        nome_completo = obj.usuario.get_full_name()
        if nome_completo:
            return f"{nome_completo} ({obj.usuario.username})"
        return obj.usuario.username
    usuario_nome.short_description = 'Usuário'
    
    def status_token(self, obj):
        """Mostra status do token OAuth do usuário para esta conta"""
        try:
            token = TokenGoogleCalendar.objects.get(
                usuario=obj.usuario, 
                conta_google=obj.conta_google
            )
            if token.is_expired:
                return format_html(
                    '<span style="color: orange;">⚠️ Expirado</span>'
                )
            else:
                return format_html(
                    '<span style="color: green;">✅ Válido</span>'
                )
        except TokenGoogleCalendar.DoesNotExist:
            return format_html(
                '<span style="color: red;">❌ Não Autorizado</span>'
            )
    status_token.short_description = 'Status OAuth'
    
    actions = ['conceder_acesso_completo', 'conceder_acesso_leitura', 'revogar_permissoes']
    
    def conceder_acesso_completo(self, request, queryset):
        """Concede acesso total (todas as permissões) às permissões selecionadas"""
        total = queryset.update(
            pode_visualizar=True,
            pode_criar=True,
            pode_editar=True,
            pode_excluir=True
        )
        self.message_user(
            request,
            f'Acesso completo concedido para {total} permissão(ões)!',
            messages.SUCCESS
        )
    conceder_acesso_completo.short_description = 'Conceder acesso completo'
    
    def conceder_acesso_leitura(self, request, queryset):
        """Concede apenas permissão de visualização"""
        total = queryset.update(
            pode_visualizar=True,
            pode_criar=False,
            pode_editar=False,
            pode_excluir=False
        )
        self.message_user(
            request,
            f'Acesso de leitura concedido para {total} permissão(ões)!',
            messages.SUCCESS
        )
    conceder_acesso_leitura.short_description = 'Conceder apenas leitura'
    
    def revogar_permissoes(self, request, queryset):
        """Revoga todas as permissões (mantém apenas o registro)"""
        total = queryset.update(
            pode_visualizar=False,
            pode_criar=False,
            pode_editar=False,
            pode_excluir=False
        )
        self.message_user(
            request,
            f'Permissões revogadas para {total} usuário(s)!',
            messages.WARNING
        )
    revogar_permissoes.short_description = 'Revogar todas as permissões'


@admin.register(TokenGoogleCalendar)
class TokenGoogleCalendarAdmin(admin.ModelAdmin):
    """
    Admin para monitoramento de tokens OAuth das contas Google.
    Principalmente para auditoria e troubleshooting.
    """
    list_display = [
        'usuario', 'conta_google_nome', 'status_token', 'expires_at', 
        'dias_para_expirar', 'ultima_renovacao', 'data_atualizacao'
    ]
    list_filter = [
        'conta_google__conta', 'expires_at', 
        'data_criacao', 'ultima_renovacao'
    ]
    search_fields = [
        'usuario__username', 'usuario__first_name', 'usuario__last_name',
        'conta_google__nome', 'conta_google__email_google'
    ]
    ordering = ['-data_atualizacao']
    
    # Campos sensíveis não devem ser editáveis
    readonly_fields = [
        'access_token_resumo', 'refresh_token_resumo', 'expires_at',
        'data_criacao', 'data_atualizacao', 'ultima_renovacao'
    ]
    
    fieldsets = (
        ('Associação', {
            'fields': ('usuario', 'conta_google')
        }),
        ('Status do Token', {
            'fields': (
                'access_token_resumo', 'refresh_token_resumo', 
                'expires_at', 'ultima_renovacao'
            ),
            'description': 'Informações do token OAuth (sensíveis - apenas resumo)'
        }),
        ('Metadados', {
            'fields': ('data_criacao', 'data_atualizacao'),
            'classes': ('collapse',)
        })
    )
    
    def conta_google_nome(self, obj):
        """Nome da conta Google"""
        return f"{obj.conta_google.nome} ({obj.conta_google.conta.nome})"
    conta_google_nome.short_description = 'Conta Google'
    
    def status_token(self, obj):
        """Status visual do token"""
        if obj.is_expired:
            return format_html(
                '<span style="color: red; font-weight: bold;">⚠️ EXPIRADO</span>'
            )
        elif obj.dias_para_expirar <= 7:
            return format_html(
                '<span style="color: orange; font-weight: bold;">⚠️ Expira em breve</span>'
            )
        else:
            return format_html(
                '<span style="color: green; font-weight: bold;">✅ VÁLIDO</span>'
            )
    status_token.short_description = 'Status'
    
    def dias_para_expirar(self, obj):
        """Quantos dias faltam para expirar"""
        dias = obj.dias_para_expirar
        if dias < 0:
            return format_html('<span style="color: red;">Expirado há {} dias</span>', abs(dias))
        elif dias <= 7:
            return format_html('<span style="color: orange;">{} dias</span>', dias)
        else:
            return f"{dias} dias"
    dias_para_expirar.short_description = 'Dias p/ Expirar'
    
    def access_token_resumo(self, obj):
        """Mostra apenas início e fim do access token por segurança"""
        if obj.access_token:
            token = obj.access_token
            if len(token) > 20:
                return f"{token[:10]}...{token[-10:]}"
            return token[:10] + "..."
        return "Não disponível"
    access_token_resumo.short_description = 'Access Token (resumo)'
    
    def refresh_token_resumo(self, obj):
        """Mostra apenas início e fim do refresh token por segurança"""
        if obj.refresh_token:
            token = obj.refresh_token
            if len(token) > 20:
                return f"{token[:10]}...{token[-10:]}"
            return token[:10] + "..."
        return "Não disponível"
    refresh_token_resumo.short_description = 'Refresh Token (resumo)'
    
    actions = ['renovar_tokens_expirados']
    
    def renovar_tokens_expirados(self, request, queryset):
        """Tenta renovar tokens expirados selecionados"""
        tokens_expirados = queryset.filter(expires_at__lt=tz.now())
        renovados = 0
        erros = 0
        
        for token in tokens_expirados:
            try:
                # Importa serviço de compatibilidade para renovação
                from .services import GoogleCalendarCompatibilityService
                GoogleCalendarCompatibilityService._refresh_token(token, token.conta_google)
                renovados += 1
            except Exception as e:
                erros += 1
        
        if renovados > 0:
            self.message_user(
                request,
                f'{renovados} token(s) renovado(s) com sucesso!',
                messages.SUCCESS
            )
        if erros > 0:
            self.message_user(
                request,
                f'{erros} token(s) falharam na renovação.',
                messages.ERROR
            )
    renovar_tokens_expirados.short_description = 'Tentar renovar tokens expirados'
    
    def has_add_permission(self, request):
        """Tokens são criados apenas via OAuth, não manualmente"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Permitir exclusão apenas para limpeza"""
        return request.user.is_superuser


@admin.register(GoogleApiToken)
class GoogleApiTokenLegadoAdmin(admin.ModelAdmin):
    """
    Admin para tokens legado (compatibilidade).
    Usado apenas para monitoramento durante migração.
    """
    list_display = [
        'usuario', 'migrado', 'data_migracao', 'expires_at'
    ]
    list_filter = ['migrado', 'data_migracao', 'expires_at']
    search_fields = ['usuario__username', 'usuario__first_name', 'usuario__last_name']
    ordering = ['-data_migracao', 'usuario__username']
    
    readonly_fields = ['access_token', 'refresh_token', 'expires_at', 'migrado', 'data_migracao']
    
    def has_add_permission(self, request):
        """Não permitir criação manual de tokens legado"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """Apenas leitura"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Permitir exclusão apenas para superusuários após migração"""
        return request.user.is_superuser and obj and obj.migrado