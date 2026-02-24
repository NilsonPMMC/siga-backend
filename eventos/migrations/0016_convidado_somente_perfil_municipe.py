# Convidado: relação apenas com PerfilMunicipe (remove FK municipe)

from django.db import migrations, models
import django.db.models.deletion


def preencher_perfil_municipe(apps, schema_editor):
    Convidado = apps.get_model('eventos', 'Convidado')
    PerfilMunicipe = apps.get_model('atendimentos', 'PerfilMunicipe')

    for c in Convidado.objects.select_related('municipe', 'evento').filter(perfil_municipe__isnull=True):
        m = c.municipe
        conta = c.evento.conta_id
        perfil, _ = PerfilMunicipe.objects.get_or_create(
            municipe_id=m.id,
            conta_id=conta,
            defaults={
                'cargo': m.cargo or None,
                'instituicao': m.orgao or None,
                'departamento': None,
                'tratamento': m.tratamento or None,
                'ativo': True,
            }
        )
        c.perfil_municipe_id = perfil.id
        c.save(update_fields=['perfil_municipe_id'])


def reverter(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0032_migrar_cargo_orgao_para_perfil'),
        ('eventos', '0015_convidado_perfil_municipe'),
    ]

    operations = [
        migrations.RunPython(preencher_perfil_municipe, reverter),
        migrations.AlterUniqueTogether(
            name='convidado',
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name='convidado',
            name='municipe',
        ),
        migrations.AlterField(
            model_name='convidado',
            name='perfil_municipe',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='convites',
                to='atendimentos.perfilmunicipe',
                verbose_name='Perfil (Cargo/Órgão) no convite'
            ),
        ),
        migrations.AlterUniqueTogether(
            name='convidado',
            unique_together={('evento', 'perfil_municipe')},
        ),
    ]
