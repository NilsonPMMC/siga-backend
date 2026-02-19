# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0028_adicionar_campos_tramitacao_sinapse'),
    ]

    operations = [
        migrations.AddField(
            model_name='atendimento',
            name='resumo_ia',
            field=models.TextField(blank=True, help_text='Resumo automático gerado pelo Gemini AI', null=True, verbose_name='Resumo Gerado por IA'),
        ),
    ]
