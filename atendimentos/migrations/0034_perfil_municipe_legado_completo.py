# Data migration: garante que todo Municipe tenha ao menos um PerfilMunicipe
# com dados legados (cargo, orgao->instituicao, tratamento). Compatível com MariaDB.

from django.db import migrations, transaction


def migrar_perfil_legado_completo(apps, schema_editor):
    Municipe = apps.get_model('atendimentos', 'Municipe')
    PerfilMunicipe = apps.get_model('atendimentos', 'PerfilMunicipe')
    Conta = apps.get_model('atendimentos', 'Conta')

    conta_padrao = Conta.objects.first()
    if not conta_padrao:
        print("[0034] AVISO: Nenhuma Conta no banco. Crie uma conta antes de rodar esta migração.")
        return

    total = Municipe.objects.count()
    criados = 0
    ignorados = 0
    erros = 0

    print(f"[0034] Iniciando migração de perfis legados. Total de municipes: {total}")

    with transaction.atomic():
        for i, m in enumerate(Municipe.objects.all().order_by('id'), start=1):
            try:
                # Já tem pelo menos um perfil? Não duplicar.
                if m.perfis.exists():
                    ignorados += 1
                    if i % 100 == 0 or i == total:
                        print(f"[0034] Progresso: {i}/{total} (criados: {criados}, ignorados: {ignorados})")
                    continue

                # Primeira conta do munícipe ou conta padrão
                primeira_conta = m.contas.order_by('id').first()
                conta = primeira_conta or conta_padrao

                PerfilMunicipe.objects.create(
                    municipe=m,
                    conta=conta,
                    cargo=(m.cargo or '').strip() or None,
                    instituicao=(m.orgao or '').strip() or None,
                    departamento=None,
                    tratamento=(m.tratamento or '').strip() or None,
                    ativo=True,
                )
                criados += 1

                if i % 100 == 0 or i == total:
                    print(f"[0034] Progresso: {i}/{total} (criados: {criados}, ignorados: {ignorados})")
            except Exception as e:
                erros += 1
                print(f"[0034] ERRO municipe id={m.id} ({m.nome_completo}): {e}")
                raise

    print(f"[0034] Concluído. Criados: {criados}, já tinham perfil: {ignorados}, erros: {erros}")


def reverter(apps, schema_editor):
    # Opcional: não remove perfis para não perder dados
    print("[0034] Reversão: perfis não foram removidos (preservar dados).")


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0033_atendimento_origem_registro_visita_usuario_destino'),
    ]

    operations = [
        migrations.RunPython(migrar_perfil_legado_completo, reverter),
    ]
