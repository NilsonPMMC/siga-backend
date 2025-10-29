import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from atendimentos.models import Municipe, CategoriaContato
import os

class Command(BaseCommand):
    help = (
        'Gera uma planilha Excel (.xlsx) de servidores a partir de uma categoria e cargos específicos. '
        'Exemplo de uso: python manage.py relatorio_servidores "SERVIDOR(A)" --cargos "secretario" "diretor" "coordenador"'
    )

    def add_arguments(self, parser):
        # Argumento 1: O nome da categoria a ser filtrada (entre aspas se tiver espaços ou caracteres especiais)
        parser.add_argument(
            'nome_categoria', 
            type=str, 
            help='O nome exato da Categoria de Contato a ser filtrada.'
        )
        
        # Argumento 2: Uma lista de termos para buscar nos cargos.
        # O nargs='+' permite passar múltiplos valores (ex: --cargos secretario diretor)
        parser.add_argument(
            '--cargos',
            nargs='+',  # Aceita um ou mais valores
            type=str,
            required=True,
            help='Uma ou mais palavras-chave para buscar no campo de cargo (não diferencia maiúsculas/minúsculas).'
        )

    def handle(self, *args, **options):
        nome_categoria = options['nome_categoria']
        cargos_desejados = options['cargos']

        self.stdout.write(self.style.SUCCESS(f'Iniciando a geração do relatório...'))
        self.stdout.write(f'  - Categoria: "{nome_categoria}"')
        self.stdout.write(f'  - Termos de Cargo: {cargos_desejados}')

        # Valida se a categoria existe
        try:
            categoria = CategoriaContato.objects.get(nome__iexact=nome_categoria)
        except CategoriaContato.DoesNotExist:
            raise CommandError(f'A categoria "{nome_categoria}" não foi encontrada no banco de dados.')
        except CategoriaContato.MultipleObjectsReturned:
            raise CommandError(f'Mais de uma categoria encontrada para "{nome_categoria}". Verifique seus dados.')

        # --- Filtros Aprimorados ---
        # Cria uma lista de objetos Q para a consulta, um para cada termo de cargo
        query_cargos = Q()
        for cargo in cargos_desejados:
            # Usamos |= (OR) para que ele encontre munícipes que tenham QUALQUER um dos cargos
            query_cargos |= Q(cargo__icontains=cargo)
        
        try:
            # A consulta agora usa o objeto 'categoria' validado
            municipes = Municipe.objects.filter(
                categoria=categoria,
                ativo=True
            ).filter(query_cargos).distinct() # .distinct() para evitar duplicatas se um cargo corresponder a múltiplos termos
        
        except Exception as e:
            raise CommandError(f'Ocorreu um erro ao consultar o banco de dados: {e}')

        if not municipes.exists():
            self.stdout.write(self.style.WARNING('Nenhum munícipe encontrado com os critérios especificados.'))
            return

        self.stdout.write(f'Encontrados {municipes.count()} munícipes. Preparando a planilha...')

        dados_para_planilha = []
        for municipe in municipes:
            telefone_principal = ''
            if municipe.telefones and len(municipe.telefones) > 0:
                primeiro_telefone = municipe.telefones[0]
                telefone_principal = primeiro_telefone.get('numero', 'Não informado')

            dados_para_planilha.append({
                'nome_completo': municipe.nome_completo,
                'cargo': municipe.cargo,
                'telefone': telefone_principal,
            })

        df = pd.DataFrame(dados_para_planilha)

        export_dir = 'exports'
        if not os.path.exists(export_dir):
            os.makedirs(export_dir)

        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        file_name = f'relatorio_servidores_{timestamp}.xlsx'
        file_path = os.path.join(export_dir, file_name)

        try:
            df.to_excel(file_path, index=False)
        except Exception as e:
             raise CommandError(f'Erro ao salvar a planilha: {e}')

        self.stdout.write(self.style.SUCCESS(f'Relatório gerado com sucesso! Salvo em: "{file_path}"'))