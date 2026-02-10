# atendimentos/management/commands/vincular_contas_municipes.py

from django.core.management.base import BaseCommand
from django.db import transaction
from atendimentos.models import Municipe, Conta

# Para a barra de progresso
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterator, *args, **kwargs):
        return iterator

class Command(BaseCommand):
    help = 'Atualiza TODOS os munícipes para estarem vinculados apenas às contas "GABINETE DA PREFEITA" e "GABINETE DO VICE-PREFEITO".'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('--- INICIANDO ATUALIZAÇÃO EM MASSA DE VÍNCULOS DE CONTAS ---'))
        self.stdout.write(self.style.WARNING('ATENÇÃO: Esta operação irá sobrescrever os vínculos de contas de TODOS os munícipes.'))

        # Nomes das contas que serão vinculadas
        nomes_das_contas = ["GABINETE DA PREFEITA", "VICE-PREFEITO"]
        
        contas_para_vincular = []
        try:
            # Busca ou cria as contas para garantir que elas existam
            for nome in nomes_das_contas:
                conta, criada = Conta.objects.get_or_create(nome=nome)
                if criada:
                    self.stdout.write(f'Conta "{nome}" não existia e foi criada.')
                contas_para_vincular.append(conta)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Erro ao buscar ou criar as contas: {e}'))
            return

        self.stdout.write(f'Vinculando todos os munícipes às contas: {", ".join(nomes_das_contas)}')

        try:
            # Pega o total de munícipes para a barra de progresso
            total_municipes = Municipe.objects.count()
            if total_municipes == 0:
                self.stdout.write(self.style.WARNING('Nenhum munícipe encontrado no banco de dados. Operação concluída sem alterações.'))
                return
            
            # Itera sobre todos os munícipes com uma barra de progresso
            # O uso de iterator() ajuda a otimizar o uso de memória em grandes volumes de dados
            for municipe in tqdm(Municipe.objects.iterator(), total=total_municipes, desc="Atualizando Munícipes"):
                # O método set() é a forma mais eficiente de definir relações ManyToMany.
                # Ele limpa os vínculos antigos e adiciona os novos em uma única operação.
                municipe.contas.set(contas_para_vincular)

            self.stdout.write(self.style.SUCCESS('\n----------------------------------------------------'))
            self.stdout.write(self.style.SUCCESS(f'Operação concluída com sucesso!'))
            self.stdout.write(self.style.SUCCESS(f'{total_municipes} registros de munícipes foram atualizados.'))

        except Exception as e:
            self.stderr.write(self.style.ERROR(f'\nOcorreu um erro durante a atualização em massa: {e}'))
            self.stderr.write(self.style.ERROR('A operação foi interrompida. Verifique o erro e tente novamente.'))