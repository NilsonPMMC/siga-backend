# Convidado: FK opcional para PerfilMunicipe (cargo/órgão no convite)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0032_migrar_cargo_orgao_para_perfil'),
        ('eventos', '0014_alter_evento_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='convidado',
            name='perfil_municipe',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='convites',
                to='atendimentos.perfilmunicipe',
                verbose_name='Perfil (Cargo/Órgão) no convite'
            ),
        ),
    ]
