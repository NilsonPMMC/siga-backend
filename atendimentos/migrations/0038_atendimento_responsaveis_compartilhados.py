# Generated manually
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('atendimentos', '0037_add_municipe_ia_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='atendimento',
            name='responsaveis_compartilhados',
            field=models.ManyToManyField(
                blank=True,
                help_text='Usuários que também podem gerir este atendimento (compartilhamento).',
                related_name='atendimentos_compartilhados',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Co-responsáveis (compartilhado)'
            ),
        ),
    ]
