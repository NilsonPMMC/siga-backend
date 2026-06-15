from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("atendimentos", "0043_automacao_aniversario_conta"),
    ]

    operations = [
        migrations.AddField(
            model_name="automacaoaniversarioconta",
            name="alerta_assunto_template",
            field=models.CharField(
                default="Relação de aniversariantes de {{ data_aniversario|date:'d/m/Y' }} - {{ conta_nome }}",
                help_text="Suporta placeholders Django Template.",
                max_length=255,
                verbose_name="Template de assunto (alerta gestores)",
            ),
        ),
        migrations.AddField(
            model_name="automacaoaniversarioconta",
            name="alerta_categoria",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Categoria usada no relatório consolidado. Se vazio, considera o mailing completo vinculado à conta.",
                max_length=100,
                verbose_name="Categoria para alerta de gestores",
            ),
        ),
        migrations.AddField(
            model_name="automacaoaniversarioconta",
            name="alerta_corpo_template",
            field=models.TextField(
                default="Prezados,\n\nSegue relatório consolidado de aniversariantes da categoria {{ categoria }} para {{ data_aniversario|date:'d/m/Y' }}.\n\nConta: {{ conta_nome }}\nTotal de aniversariantes: {{ total_aniversariantes }}\n",
                help_text="Suporta placeholders Django Template como {{ conta_nome }}, {{ categoria }}, {{ data_aniversario|date:'d/m/Y' }} e {{ total_aniversariantes }}.",
                verbose_name="Template do corpo (alerta gestores)",
            ),
        ),
        migrations.AddField(
            model_name="automacaoaniversarioconta",
            name="alerta_gestores_ativo",
            field=models.BooleanField(
                default=False,
                help_text="Se marcado, envia relatório consolidado de aniversariantes para usuários internos.",
                verbose_name="Alerta para gestores ativo?",
            ),
        ),
        migrations.AddField(
            model_name="automacaoaniversarioconta",
            name="alerta_usuarios",
            field=models.ManyToManyField(
                blank=True,
                help_text="Selecione os usuários internos que receberão o alerta consolidado.",
                related_name="alertas_aniversario_recebidos",
                to="auth.user",
                verbose_name="Usuários destinatários do alerta",
            ),
        ),
    ]
