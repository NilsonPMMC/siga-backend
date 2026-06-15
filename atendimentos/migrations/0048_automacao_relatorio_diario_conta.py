from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("atendimentos", "0047_campanhalogenvio_email_real_enviado"),
    ]

    operations = [
        migrations.CreateModel(
            name="AutomacaoRelatorioDiarioConta",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ativo", models.BooleanField(default=True, help_text="Se desmarcado, a rotina diária ignora esta conta.", verbose_name="Automação ativa?")),
                ("smtp_host", models.CharField(default="cloud77.mailgrid.net.br", max_length=255, verbose_name="SMTP host")),
                ("smtp_port", models.PositiveIntegerField(default=587, verbose_name="SMTP porta")),
                ("smtp_use_tls", models.BooleanField(default=True, verbose_name="Usar TLS")),
                ("smtp_use_ssl", models.BooleanField(default=False, verbose_name="Usar SSL")),
                ("env_var_smtp_user", models.CharField(help_text="Ex.: SMTP_USER_PREFEITA", max_length=100, verbose_name="Variável de ambiente - usuário SMTP")),
                ("env_var_smtp_pass", models.CharField(help_text="Ex.: SMTP_PASS_PREFEITA", max_length=100, verbose_name="Variável de ambiente - senha SMTP")),
                ("from_email", models.EmailField(blank=True, help_text="Se vazio, usa o usuário SMTP da conta.", max_length=254, null=True, verbose_name="Remetente visível (opcional)")),
                ("assunto_template", models.CharField(default="Relatórios diários - {{ conta_nome }} - {{ data_referencia|date:'d/m/Y' }}", help_text="Suporta placeholders Django Template.", max_length=255, verbose_name="Template de assunto")),
                ("corpo_template", models.TextField(default="Prezados,\n\nSegue em anexo os relatórios do dia {{ data_referencia|date:'d/m/Y' }}.\n\nConta: {{ conta_nome }}\n", help_text="Suporta placeholders como {{ conta_nome }} e {{ data_referencia|date:'d/m/Y' }}.", verbose_name="Template do corpo")),
                ("dias_offset", models.PositiveSmallIntegerField(default=0, help_text="0 = usa o dia da execução; 1 = usa o dia anterior (útil quando o cron roda de manhã).", verbose_name="Dias de deslocamento da data")),
                ("enviar_relatorio_atendimentos", models.BooleanField(default=True, verbose_name="Enviar relatório de atendimentos?")),
                ("enviar_relatorio_checkins", models.BooleanField(default=True, verbose_name="Enviar relatório de check-ins?")),
                ("checkins_filtrar_por_conta", models.BooleanField(default=False, help_text="Se desmarcado, o relatório de check-ins considera todas as contas (como na URL sem conta_id).", verbose_name="Filtrar check-ins pela conta?")),
                ("data_atualizacao", models.DateTimeField(auto_now=True)),
                ("data_criacao", models.DateTimeField(auto_now_add=True)),
                ("conta", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="automacao_relatorio_diario", to="atendimentos.conta", verbose_name="Conta/Gabinete")),
                ("destinatarios", models.ManyToManyField(blank=True, help_text="Usuários internos que receberão os relatórios em PDF.", related_name="relatorios_diarios_recebidos", to=settings.AUTH_USER_MODEL, verbose_name="Usuários destinatários")),
            ],
            options={
                "verbose_name": "Automação de Relatório Diário por Conta",
                "verbose_name_plural": "Automações de Relatório Diário por Conta",
                "ordering": ["conta__nome"],
            },
        ),
    ]
