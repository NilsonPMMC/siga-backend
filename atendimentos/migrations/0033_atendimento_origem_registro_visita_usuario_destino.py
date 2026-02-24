# Generated manually: Atendimento.origem e RegistroVisita.usuario_destino

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0032_migrar_cargo_orgao_para_perfil'),
    ]

    operations = [
        migrations.AddField(
            model_name='atendimento',
            name='origem',
            field=models.CharField(
                choices=[
                    ('PRESENCIAL', 'Presencial'),
                    ('TELEFONE', 'Telefone'),
                    ('EMAIL', 'E-mail'),
                    ('WHATSAPP', 'WhatsApp'),
                ],
                default='PRESENCIAL',
                max_length=20,
                verbose_name='Origem do Atendimento',
            ),
        ),
        migrations.AddField(
            model_name='registrovisita',
            name='usuario_destino',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='visitas_destino',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Responsável / Usuário Destino',
            ),
        ),
    ]
