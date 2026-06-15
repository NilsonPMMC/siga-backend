from django.core.management.base import BaseCommand

from atendimentos.services.mescla_categoria_assunto import executar_mescla


class Command(BaseCommand):
    help = (
        'Fase 8: mescla CategoriaAtendimento (M2M legado) em AssuntoAtendimento (FK). '
        'Use --dry-run antes de --apply.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Grava alterações no banco (sem esta flag, apenas simula).',
        )
        parser.add_argument(
            '--limpar-m2m',
            action='store_true',
            help='Remove vínculos M2M de categorias após definir assunto.',
        )
        parser.add_argument(
            '--desativar-categorias',
            action='store_true',
            help='Marca todas CategoriaAtendimento como inativas.',
        )
        parser.add_argument(
            '--preencher-outros',
            action='store_true',
            help='Atendimentos sem assunto e sem categoria recebem assunto OUTROS.',
        )
        parser.add_argument(
            '--forcar-de-categoria',
            action='store_true',
            help='Sobrescreve assunto existente quando divergir da categoria M2M.',
        )
        parser.add_argument(
            '--limite',
            type=int,
            default=None,
            help='Processa no máximo N atendimentos (teste).',
        )

    def handle(self, *args, **options):
        aplicar = options['apply']
        dry_run = not aplicar

        self.stdout.write(self.style.NOTICE('=' * 72))
        self.stdout.write(self.style.NOTICE('mesclar_categorias_em_assuntos (Fase 8)'))
        if dry_run:
            self.stdout.write(self.style.WARNING('Modo DRY-RUN — use --apply para gravar.'))
        else:
            self.stdout.write(self.style.SUCCESS('Modo APPLY — alterações serão gravadas.'))

        resultado = executar_mescla(
            aplicar=aplicar,
            limpar_m2m=options['limpar_m2m'],
            desativar_categorias=options['desativar_categorias'],
            preencher_sem_assunto_outros=options['preencher_outros'],
            forcar_de_categoria=options['forcar_de_categoria'],
            limite=options['limite'],
        )
        stats = resultado['stats']

        self.stdout.write(self.style.NOTICE('Assuntos extras garantidos no cadastro:'))
        for nome, codigo, ordem in resultado['assuntos_extras']:
            self.stdout.write(f'  - {nome} ({codigo}) ordem={ordem}')

        self.stdout.write(self.style.NOTICE('--- Resultado ---'))
        for chave, valor in stats.items():
            self.stdout.write(f'  {chave}: {valor}')

        if resultado['amostra_preenchidos']:
            self.stdout.write(self.style.NOTICE('Amostra (categoria → assunto):'))
            for linha in resultado['amostra_preenchidos']:
                self.stdout.write(f'  {linha}')

        if resultado['conflitos']:
            self.stdout.write(
                self.style.WARNING(
                    f"Conflitos ({len(resultado['conflitos'])}) — assunto já definido:"
                )
            )
            for linha in resultado['conflitos'][:20]:
                self.stdout.write(f'  {linha}')
            if len(resultado['conflitos']) > 20:
                self.stdout.write(f'  ... e mais {len(resultado["conflitos"]) - 20}')

        self.stdout.write(self.style.NOTICE('=' * 72))
