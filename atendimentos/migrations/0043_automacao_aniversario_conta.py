from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("atendimentos", "0042_remove_municipe_categoria"),
    ]

    operations = [
        migrations.CreateModel(
            name="AutomacaoAniversarioConta",
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
                ("assunto_template", models.CharField(default="Feliz aniversário, {{ nome_completo }}!", help_text="Suporta placeholders Django Template.", max_length=255, verbose_name="Template de assunto")),
                ("corpo_template", models.TextField(default="Prezado(a) {{ nome_completo }},\n\nEm nome de {{ conta_nome }}, desejamos um feliz aniversário.\nSaúde, paz e realizações neste novo ciclo.", help_text="Use placeholders como {{ nome_completo }}, {{ conta_nome }}, {{ conta_nome_titular }} e {{ data_alvo|date:'d/m/Y' }}.", verbose_name="Template do corpo")),
                ("arte", models.ImageField(blank=True, null=True, upload_to="aniversarios/artes/", verbose_name="Arte de aniversário (inline)")),
                ("data_atualizacao", models.DateTimeField(auto_now=True)),
                ("data_criacao", models.DateTimeField(auto_now_add=True)),
                ("conta", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="automacao_aniversario", to="atendimentos.conta", verbose_name="Conta/Gabinete")),
            ],
            options={
                "verbose_name": "Automação de Aniversário por Conta",
                "verbose_name_plural": "Automações de Aniversário por Conta",
                "ordering": ["conta__nome"],
            },
        ),
    ]
