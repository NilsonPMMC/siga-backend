# Generated manually - PerfilMunicipe para múltiplos cargos/órgãos por munícipe

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0030_municipe_auditoria_ia'),
    ]

    operations = [
        migrations.CreateModel(
            name='PerfilMunicipe',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cargo', models.CharField(blank=True, max_length=150, null=True, verbose_name='Cargo')),
                ('instituicao', models.CharField(blank=True, max_length=150, null=True, verbose_name='Instituição/Órgão')),
                ('departamento', models.CharField(blank=True, max_length=150, null=True, verbose_name='Departamento')),
                ('tratamento', models.CharField(blank=True, help_text='Ex: Sr., Dr., Vossa Excelência', max_length=50, null=True, verbose_name='Tratamento')),
                ('ativo', models.BooleanField(default=True, verbose_name='Ativo')),
                ('conta', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='perfis_municipe', to='atendimentos.conta', verbose_name='Conta/Gabinete')),
                ('municipe', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='perfis', to='atendimentos.municipe', verbose_name='Munícipe')),
            ],
            options={
                'verbose_name': 'Perfil do Munícipe',
                'verbose_name_plural': 'Perfis do Munícipe',
                'ordering': ['conta', 'cargo'],
            },
        ),
    ]
