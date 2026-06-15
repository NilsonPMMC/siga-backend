from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("atendimentos", "0044_automacao_aniversario_alerta_gestores"),
    ]

    operations = [
        migrations.AlterField(
            model_name="automacaoaniversarioconta",
            name="alerta_categoria",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Categoria usada no relatório consolidado. Se vazio, considera o mailing completo vinculado à conta.",
                max_length=100,
                verbose_name="Categoria para alerta de gestores",
            ),
        ),
    ]
