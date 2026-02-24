# Renomeia perfil_municipe -> perfil e adiciona municipe opcional (fallback)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0032_migrar_cargo_orgao_para_perfil'),
        ('eventos', '0016_convidado_somente_perfil_municipe'),
    ]

    operations = [
        migrations.RenameField(
            model_name='convidado',
            old_name='perfil_municipe',
            new_name='perfil',
        ),
        migrations.AlterUniqueTogether(
            name='convidado',
            unique_together={('evento', 'perfil')},
        ),
        migrations.AddField(
            model_name='convidado',
            name='municipe',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='convites_legado',
                to='atendimentos.municipe',
                verbose_name='Munícipe (fallback)'
            ),
        ),
    ]
