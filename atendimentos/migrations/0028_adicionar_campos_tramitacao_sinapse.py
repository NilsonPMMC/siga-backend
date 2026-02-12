# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0027_conta_assinatura_eletronica'),
    ]

    operations = [
        # Adicionar campos em Tramitacao
        migrations.AddField(
            model_name='tramitacao',
            name='status_anterior',
            field=models.CharField(blank=True, choices=[('ABERTO', 'Aberto'), ('EM_ANALISE', 'Em Análise'), ('ENCAMINHADO', 'Encaminhado'), ('CONCLUIDO', 'Concluído'), ('ARQUIVADO', 'Arquivado')], max_length=20, null=True, verbose_name='Status Anterior'),
        ),
        migrations.AddField(
            model_name='tramitacao',
            name='status_novo',
            field=models.CharField(blank=True, choices=[('ABERTO', 'Aberto'), ('EM_ANALISE', 'Em Análise'), ('ENCAMINHADO', 'Encaminhado'), ('CONCLUIDO', 'Concluído'), ('ARQUIVADO', 'Arquivado')], max_length=20, null=True, verbose_name='Status Novo'),
        ),
        migrations.AddField(
            model_name='tramitacao',
            name='alterou_status',
            field=models.BooleanField(default=False, verbose_name='Esta tramitação alterou o status?'),
        ),
        migrations.AddField(
            model_name='tramitacao',
            name='encaminhado_para_sinapse_id',
            field=models.IntegerField(blank=True, null=True, verbose_name='ID Sinapse (Secretaria/Órgão)'),
        ),
        migrations.AddField(
            model_name='tramitacao',
            name='encaminhado_para_nome',
            field=models.CharField(blank=True, max_length=255, null=True, verbose_name='Nome do Destino (Sinapse)'),
        ),
        migrations.AddField(
            model_name='tramitacao',
            name='encaminhado_para_tipo',
            field=models.CharField(blank=True, max_length=50, null=True, verbose_name='Tipo (Secretaria/Setor/etc)'),
        ),
        
        # Criar modelo SinapseSecretaria
        migrations.CreateModel(
            name='SinapseSecretaria',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sinapse_id', models.IntegerField(unique=True, verbose_name='ID Sinapse')),
                ('nome', models.CharField(max_length=255, verbose_name='Nome da Secretaria/Órgão')),
                ('sigla', models.CharField(blank=True, max_length=50, null=True, verbose_name='Sigla')),
                ('tipo', models.CharField(max_length=50, verbose_name='Tipo (Secretaria, Setor, etc)')),
                ('hierarquia', models.JSONField(blank=True, help_text='Estrutura hierárquica completa da API Sinapse', null=True)),
                ('ativo', models.BooleanField(default=True, verbose_name='Está ativo?')),
                ('data_atualizacao', models.DateTimeField(auto_now=True, verbose_name='Última Atualização')),
            ],
            options={
                'verbose_name': 'Secretaria Sinapse',
                'verbose_name_plural': 'Secretarias Sinapse',
                'ordering': ['nome'],
            },
        ),
    ]
