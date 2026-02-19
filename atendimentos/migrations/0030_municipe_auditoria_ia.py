# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0029_atendimento_resumo_ia'),
    ]

    operations = [
        migrations.AddField(
            model_name='municipe',
            name='auditoria_ia',
            field=models.JSONField(blank=True, default=dict, help_text='Dados de auditoria gerados pela IA: nota de qualidade, classificação, sugestões de correção.', null=True, verbose_name='Auditoria de Qualidade (IA)'),
        ),
    ]
