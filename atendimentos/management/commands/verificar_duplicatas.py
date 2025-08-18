import uuid
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db.models import Count
from atendimentos.models import Municipe

# Importamos a unidecode, que usaremos na busca por nome
try:
    from unidecode import unidecode
except ImportError:
    raise CommandError("A biblioteca 'unidecode' não está instalada. Por favor, rode 'pip install unidecode'.")

class Command(BaseCommand):
    help = 'Verifica e agrupa contatos com possíveis duplicatas.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Iniciando verificação de duplicatas...'))

        Municipe.objects.update(grupo_duplicado=None)
        self.stdout.write('Etiquetas de duplicatas antigas foram limpas.')
        
        total_grupos_encontrados = 0

        # --- Verificação por CPF e Email ---
        for campo in ['cpf', 'email']:
            self.stdout.write(f"Verificando duplicatas por: {campo}...")
            # Encontra valores que aparecem mais de uma vez, ignorando nulos/vazios
            duplicatas = Municipe.objects.exclude(**{f'{campo}__isnull': True}).exclude(**{f'{campo}__exact': ''})\
                .values(campo).annotate(total=Count('id')).filter(total__gt=1)

            for duplicata in duplicatas:
                valor_duplicado = duplicata[campo]
                contatos_para_agrupar = Municipe.objects.filter(**{campo: valor_duplicado}, grupo_duplicado__isnull=True)
                
                if contatos_para_agrupar.count() > 1:
                    novo_grupo_id = uuid.uuid4()
                    contatos_para_agrupar.update(grupo_duplicado=novo_grupo_id)
                    total_grupos_encontrados += 1
        
        # --- Verificação por Nome Completo Normalizado ---
        self.stdout.write(f"Verificando duplicatas por: nome...")
        nomes_map = defaultdict(list)
        for municipe in Municipe.objects.exclude(nome_completo__exact='').iterator():
            nome_normalizado = unidecode(municipe.nome_completo).strip().lower()
            nomes_map[nome_normalizado].append(municipe.id)

        ids_duplicados_por_nome = [ids for nome, ids in nomes_map.items() if len(ids) > 1]
        for ids_para_agrupar in ids_duplicados_por_nome:
            contatos = Municipe.objects.filter(id__in=ids_para_agrupar, grupo_duplicado__isnull=True)
            if contatos.count() > 1:
                novo_grupo_id = uuid.uuid4()
                contatos.update(grupo_duplicado=novo_grupo_id)
                total_grupos_encontrados += 1

        # --- Verificação por Telefone ---
        self.stdout.write(f"Verificando duplicatas por: telefones...")
        numeros_map = defaultdict(list)
        municipes_com_telefone = Municipe.objects.exclude(telefones__isnull=True).exclude(telefones__exact=[]).filter(grupo_duplicado__isnull=True)

        for m in municipes_com_telefone:
            for tel_info in m.telefones:
                if isinstance(tel_info, dict) and tel_info.get('numero'):
                    numeros_map[tel_info['numero']].append(m.id)

        ids_para_agrupar_flat = [ids for numero, ids in numeros_map.items() if len(ids) > 1]
        for ids_para_agrupar in ids_para_agrupar_flat:
            contatos = Municipe.objects.filter(id__in=ids_para_agrupar, grupo_duplicado__isnull=True)
            if contatos.count() > 1:
                novo_grupo_id = uuid.uuid4()
                contatos.update(grupo_duplicado=novo_grupo_id)
                total_grupos_encontrados += 1

        self.stdout.write(self.style.SUCCESS(f'Verificação concluída! {total_grupos_encontrados} grupos de possíveis duplicatas foram encontrados e etiquetados.'))