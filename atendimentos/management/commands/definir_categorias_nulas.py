# atendimentos/management/commands/definir_categorias_nulas.py

from django.core.management.base import BaseCommand
from atendimentos.models import PerfilMunicipe, CategoriaContato

class Command(BaseCommand):
    help = 'Define a categoria "_A DEFINIR" para todos os perfis (PerfilMunicipe) sem categoria'

    def handle(self, *args, **options):
        self.stdout.write("Iniciando verificação de perfis sem categoria...")

        # 1. Garante que a categoria de fallback existe
        categoria_padrao, created = CategoriaContato.objects.get_or_create(
            nome="_A DEFINIR",
            defaults={'ativa': True}
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f'Categoria "{categoria_padrao.nome}" criada com sucesso.'))
        else:
            self.stdout.write(f'Usando categoria existente: "{categoria_padrao.nome}".')

        # 2. Busca perfis com categoria NULA
        perfis_sem_cat = PerfilMunicipe.objects.filter(categoria__isnull=True)
        total = perfis_sem_cat.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nenhum perfil com categoria nula encontrado. Tudo certo!'))
            return

        self.stdout.write(f'{total} perfis encontrados sem categoria. Atualizando...')

        # 3. Atualiza em massa
        updated_count = perfis_sem_cat.update(categoria=categoria_padrao)

        self.stdout.write(self.style.SUCCESS(f'Concluído! {updated_count} perfis atualizados para "_A DEFINIR".'))