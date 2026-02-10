# Generated manually for Django 5.2.3

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0026_alter_municipe_categoria'),
    ]

    operations = [
        migrations.AddField(
            model_name='conta',
            name='assinatura_eletronica',
            field=models.ImageField(
                blank=True,
                help_text='Imagem da assinatura eletrônica para uso em ofícios (formato PNG/JPG recomendado)',
                null=True,
                upload_to='assinaturas/',
                verbose_name='Assinatura Eletrônica'
            ),
        ),
        migrations.AddField(
            model_name='conta',
            name='usar_assinatura_eletronica',
            field=models.BooleanField(
                default=False,
                help_text='Se marcado, a assinatura eletrônica será incluída nos ofícios gerados. Certifique-se de ter feito upload da imagem da assinatura.',
                verbose_name='Usar Assinatura Eletrônica em Ofícios?'
            ),
        ),
    ]
