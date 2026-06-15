from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("atendimentos", "0049_assunto_atendimento"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="automacaorelatoriodiarioconta",
            name="checkins_filtrar_por_conta",
        ),
        migrations.RemoveField(
            model_name="automacaorelatoriodiarioconta",
            name="enviar_relatorio_checkins",
        ),
    ]
