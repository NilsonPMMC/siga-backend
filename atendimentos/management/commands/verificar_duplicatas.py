import uuid
import re
import itertools
from collections import defaultdict
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from atendimentos.models import Municipe

try:
    from unidecode import unidecode
except ImportError:
    raise CommandError("A biblioteca 'unidecode' não está instalada. Por favor, rode 'pip install unidecode'.")

def normalizar_nome_para_conjunto(nome):
    """
    Função aprimorada que transforma um nome em um conjunto de palavras-chave.
    Ex: "Glaucia Cristina M. Coutinho" -> {'glaucia', 'cristina', 'm', 'coutinho'}
    """
    if not nome:
        return set()
    
    # Remove acentos e converte para minúsculas
    nome_sem_acentos = unidecode(nome).lower()
    
    # Remove caracteres que não sejam letras, números ou espaços
    nome_limpo = re.sub(r'[^a-z0-9\s]', '', nome_sem_acentos)
    
    # Palavras a serem ignoradas (artigos, preposições, etc.)
    palavras_ignoradas = {'de', 'da', 'do', 'dos', 'das', 'e'}
    
    # Divide o nome em palavras e remove as ignoradas
    palavras = {palavra for palavra in nome_limpo.split() if palavra not in palavras_ignoradas}
    
    return palavras

class Command(BaseCommand):
    help = 'Verifica duplicatas com lógica de subconjunto de nomes e agrupamento inteligente.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Iniciando verificação avançada de duplicatas...'))

        Municipe.objects.update(grupo_duplicado=None)
        self.stdout.write('Etiquetas de duplicatas antigas foram limpas.')
        
        total_grupos_encontrados = 0

        # --- ETAPA 1: Verificação por CPF (Alta Confiança) ---
        self.stdout.write("Verificando duplicatas por CPF...")
        cpfs_duplicados = Municipe.objects.exclude(cpf__isnull=True).exclude(cpf__exact='') \
            .values('cpf').annotate(total=Count('id')).filter(total__gt=1)

        ids_ja_agrupados = set()
        for item in cpfs_duplicados:
            cpf = item['cpf']
            contatos_para_agrupar = Municipe.objects.filter(cpf=cpf)
            if contatos_para_agrupar.count() > 1:
                novo_grupo_id = uuid.uuid4()
                contatos_para_agrupar.update(grupo_duplicado=novo_grupo_id)
                ids_ja_agrupados.update(contatos_para_agrupar.values_list('id', flat=True))
                total_grupos_encontrados += 1
        
        self.stdout.write(f"{total_grupos_encontrados} grupos encontrados por CPF.")

        # --- ETAPA 2: Lógica de Subconjunto de Nomes ---
        self.stdout.write("Verificando duplicatas por combinação de Nome e Contato...")

        telefone_map = defaultdict(list)
        email_map = defaultdict(list)
        
        municipes_restantes = Municipe.objects.exclude(id__in=ids_ja_agrupados)
        
        id_para_nome_conjunto = {m.id: normalizar_nome_para_conjunto(m.nome_completo) for m in municipes_restantes}

        for municipe in municipes_restantes:
            if municipe.telefones and isinstance(municipe.telefones, list):
                for tel in municipe.telefones:
                    if isinstance(tel, dict) and tel.get('numero'):
                        telefone_map[tel['numero']].append(municipe.id)
            
            if municipe.emails and isinstance(municipe.emails, list):
                for email_item in municipe.emails:
                    if isinstance(email_item, dict) and email_item.get('email'):
                        email_map[email_item['email'].lower()].append(municipe.id)

        adjacencia = defaultdict(set)
        grupos_de_contato = list(telefone_map.values()) + list(email_map.values())

        for grupo_ids in grupos_de_contato:
            if len(grupo_ids) < 2:
                continue
            
            for id1, id2 in itertools.combinations(grupo_ids, 2):
                conjunto1 = id_para_nome_conjunto.get(id1)
                conjunto2 = id_para_nome_conjunto.get(id2)

                if not conjunto1 or not conjunto2:
                    continue

                # A lógica de subconjunto!
                if conjunto1.issubset(conjunto2) or conjunto2.issubset(conjunto1):
                    adjacencia[id1].add(id2)
                    adjacencia[id2].add(id1)

        visitados = set(ids_ja_agrupados)
        for municipe_id in list(adjacencia.keys()):
            if municipe_id not in visitados:
                componente_atual = []
                pilha = [municipe_id]
                visitados.add(municipe_id)

                while pilha:
                    no_atual = pilha.pop()
                    componente_atual.append(no_atual)
                    for vizinho in adjacencia[no_atual]:
                        if vizinho not in visitados:
                            visitados.add(vizinho)
                            pilha.append(vizinho)
                
                if len(componente_atual) > 1:
                    novo_grupo_id = uuid.uuid4()
                    Municipe.objects.filter(id__in=componente_atual).update(grupo_duplicado=novo_grupo_id)
                    total_grupos_encontrados += 1

        self.stdout.write(self.style.SUCCESS(f'Verificação concluída! Total de {total_grupos_encontrados} grupos de duplicatas foram encontrados.'))
