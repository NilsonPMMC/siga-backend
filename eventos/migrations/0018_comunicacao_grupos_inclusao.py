# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("eventos", "0017_convidado_perfil_e_municipe_fallback"),
    ]

    operations = [
        migrations.AddField(
            model_name="comunicacao",
            name="grupos_inclusao",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Histórico de inclusões em lote por categoria ou lista de mailing.",
                verbose_name="Grupos de inclusão (categoria / mailing)",
            ),
        ),
    ]
