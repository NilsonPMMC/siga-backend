# Data migration: move cargo/órgão do Municipe para o primeiro PerfilMunicipe

from django.db import migrations


def migrar_para_perfil(apps, schema_editor):
    Municipe = apps.get_model('atendimentos', 'Municipe')
    PerfilMunicipe = apps.get_model('atendimentos', 'PerfilMunicipe')

    for m in Municipe.objects.all():
        # Só cria perfil se houver cargo ou órgão
        cargo = (m.cargo or '').strip()
        orgao = (m.orgao or '').strip()
        if not cargo and not orgao:
            continue

        # Usa a primeira conta vinculada ao munícipe; se não tiver, pula
        contas = m.contas.all()[:1]
        if not contas:
            continue

        conta = contas[0]
        PerfilMunicipe.objects.get_or_create(
            municipe=m,
            conta=conta,
            defaults={
                'cargo': m.cargo or None,
                'instituicao': m.orgao or None,
                'departamento': None,
                'tratamento': m.tratamento or None,
                'ativo': True,
            }
        )


def reverter_perfil(apps, schema_editor):
    # Opcional: não remove perfis para não perder dados
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0031_add_perfil_municipe'),
    ]

    operations = [
        migrations.RunPython(migrar_para_perfil, reverter_perfil),
    ]
