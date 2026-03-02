# Remove categoria from Municipe - now in PerfilMunicipe
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0041_perfilmunicipe_categoria_required'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='municipe',
            name='categoria',
        ),
    ]
