# Data migration: copy Municipe.categoria to PerfilMunicipe.categoria
from django.db import migrations, transaction


def migrar_categoria_para_perfil(apps, schema_editor):
    Municipe = apps.get_model('atendimentos', 'Municipe')
    PerfilMunicipe = apps.get_model('atendimentos', 'PerfilMunicipe')
    Conta = apps.get_model('atendimentos', 'Conta')

    conta_padrao = Conta.objects.first()
    if not conta_padrao:
        print("[0040] AVISO: Nenhuma Conta no banco.")
        return

    total = Municipe.objects.count()
    perfis_atualizados = 0
    perfis_criados = 0

    print(f"[0040] Migrando categoria para PerfilMunicipe. Total municipes: {total}")

    with transaction.atomic():
        for m in Municipe.objects.select_related('categoria').all():
            categoria_id = m.categoria_id if m.categoria_id else None
            if not categoria_id:
                continue

            perfis = list(m.perfis.all())
            if not perfis:
                # Munícipe sem perfil: cria um (usa primeira conta ou conta padrão)
                primeira_conta = m.contas.order_by('id').first()
                conta = primeira_conta or conta_padrao
                PerfilMunicipe.objects.create(
                    municipe=m,
                    conta=conta,
                    cargo=getattr(m, 'cargo', None) or None,
                    instituicao=getattr(m, 'orgao', None) or None,
                    categoria_id=categoria_id,
                    ativo=True,
                )
                perfis_criados += 1
            else:
                for p in perfis:
                    p.categoria_id = categoria_id
                    p.save()
                    perfis_atualizados += 1

    print(f"[0040] Concluído. Perfis atualizados: {perfis_atualizados}, perfis criados: {perfis_criados}")


def reverter(apps, schema_editor):
    # Não desfaz - os dados permanecem em PerfilMunicipe
    print("[0040] Reversão: dados em PerfilMunicipe.categoria não foram alterados.")


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0039_perfilmunicipe_categoria'),
    ]

    operations = [
        migrations.RunPython(migrar_categoria_para_perfil, reverter),
    ]
