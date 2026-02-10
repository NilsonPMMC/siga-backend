# atendimentos/management/commands/definir_categorias_nulas.py

from django.core.management.base import BaseCommand
from atendimentos.models import Municipe, CategoriaContato

class Command(BaseCommand):
    help = 'Define a categoria "_A DEFINIR" para todos os munícipes sem categoria'

    def handle(self, *args, **options):
        self.stdout.write("Iniciando verificação de munícipes sem categoria...")

        # 1. Garante que a categoria de fallback existe
        categoria_padrao, created = CategoriaContato.objects.get_or_create(
            nome="_A DEFINIR",
            defaults={'ativa': True}
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f'Categoria "{categoria_padrao.nome}" criada com sucesso.'))
        else:
            self.stdout.write(f'Usando categoria existente: "{categoria_padrao.nome}".')

        # 2. Busca munícipes com categoria NULA
        municipes_sem_cat = Municipe.objects.filter(categoria__isnull=True)
        total = municipes_sem_cat.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nenhum munícipe com categoria nula encontrado. Tudo certo!'))
            return

        self.stdout.write(f'{total} munícipes encontrados sem categoria. Atualizando...')

        # 3. Atualiza em massa (Muito mais rápido que loop for)
        updated_count = municipes_sem_cat.update(categoria=categoria_padrao)

        self.stdout.write(self.style.SUCCESS(f'Concluído! {updated_count} registros atualizados para "_A DEFINIR".'))