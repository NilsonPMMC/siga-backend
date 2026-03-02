# Add categoria to PerfilMunicipe (nullable for data migration)
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0038_atendimento_responsaveis_compartilhados'),
    ]

    operations = [
        migrations.AddField(
            model_name='perfilmunicipe',
            name='categoria',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='perfis_municipe',
                to='atendimentos.categoriacontato',
                verbose_name='Categoria do Contato'
            ),
        ),
    ]
