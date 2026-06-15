from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("atendimentos", "0050_remove_checkins_automacao_relatorio"),
    ]

    operations = [
        migrations.AddField(
            model_name="registrovisita",
            name="atendimento",
            field=models.ForeignKey(
                blank=True,
                help_text="Atendimento unificado gerado a partir desta visita (migração Fase 4).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="registros_visita_legado",
                to="atendimentos.atendimento",
                verbose_name="Atendimento vinculado",
            ),
        ),
    ]
