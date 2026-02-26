# Generated migration for IA fields (resumo_ia_local, vetor_ia_atendimento, auditoria_ia_status)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0034_perfil_municipe_legado_completo'),
    ]

    operations = [
        migrations.AddField(
            model_name='atendimento',
            name='resumo_ia_local',
            field=models.TextField(blank=True, help_text='Resumo consolidado gerado pela IA local considerando triagem e tramitações.', null=True, verbose_name='Resumo IA Local (Ollama)'),
        ),
        migrations.AddField(
            model_name='atendimento',
            name='vetor_ia_atendimento',
            field=models.JSONField(blank=True, help_text='Embedding para busca semântica (mxbai-embed-large).', null=True, verbose_name='Vetor IA (Embedding)'),
        ),
        migrations.AddField(
            model_name='atendimento',
            name='auditoria_ia_status',
            field=models.CharField(
                choices=[('PENDENTE', 'Pendente'), ('PROCESSADO', 'Processado'), ('ERRO', 'Erro')],
                default='PENDENTE',
                max_length=20,
                verbose_name='Status Processamento IA'
            ),
        ),
    ]
