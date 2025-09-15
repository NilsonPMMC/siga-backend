# atendimentos/management/commands/relatorio_sem_email.py

import os
import datetime
from django.core.management.base import BaseCommand
from django.db.models import Q
from atendimentos.models import Municipe, Conta
import openpyxl
from openpyxl.styles import Font, Alignment

# O nome da conta que você quer filtrar
NOME_CONTA = "GABINETE DA PREFEITA"

class Command(BaseCommand):
    help = f'Gera uma planilha Excel de contatos (munícipes) da conta "{NOME_CONTA}" que não possuem e-mail cadastrado.'

    def add_arguments(self, parser):
        # Adiciona um argumento opcional para o nome do arquivo
        parser.add_argument(
            '--arquivo',
            type=str,
            help='Nome do arquivo Excel de saída (ex: relatorio.xlsx)',
            default=None
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"--- Iniciando relatório para a conta: {NOME_CONTA} ---"))

        # Define o nome do arquivo de saída
        nome_arquivo = options['arquivo']
        if not nome_arquivo:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            nome_arquivo = f"relatorio_municipes_sem_email_{timestamp}.xlsx"

        try:
            conta = Conta.objects.get(nome=NOME_CONTA)
        except Conta.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'ERRO: A conta "{NOME_CONTA}" não foi encontrada.'))
            return

        municipes_sem_email = Municipe.objects.filter(
            contas=conta
        ).filter(
            Q(emails__isnull=True) | Q(emails__exact=[])
        ).order_by('nome_completo')

        if not municipes_sem_email.exists():
            self.stdout.write(self.style.WARNING(f"Nenhum munícipe sem e-mail foi encontrado para a conta {NOME_CONTA}."))
            return

        # --- LÓGICA PARA GERAR O EXCEL ---
        
        # Cria um novo Workbook (arquivo Excel)
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Contatos Sem Email"

        # Define o cabeçalho da planilha
        headers = ["Nome Completo", "Cargo", "Órgão/Empresa", "Telefones"]
        sheet.append(headers)

        # Aplica estilo ao cabeçalho (negrito)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")

        # Preenche a planilha com os dados
        for municipe in municipes_sem_email:
            # Formata a lista de telefones para uma string legível
            telefones_str = ""
            if isinstance(municipe.telefones, list) and municipe.telefones:
                telefones_list = [
                    f"{t.get('numero', '')} ({t.get('tipo', 'N/D')})" 
                    for t in municipe.telefones if t.get('numero')
                ]
                telefones_str = " | ".join(telefones_list)
            
            # Adiciona a linha na planilha
            row_data = [
                municipe.nome_completo,
                municipe.cargo or "Não informado",
                municipe.orgao or "Não informado",
                telefones_str or "Nenhum cadastrado"
            ]
            sheet.append(row_data)

        # Ajusta a largura das colunas
        for column_cells in sheet.columns:
            length = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = length + 2

        # Salva o arquivo Excel
        try:
            workbook.save(nome_arquivo)
            # Imprime o caminho completo do arquivo
            caminho_completo = os.path.abspath(nome_arquivo)
            self.stdout.write(self.style.SUCCESS(f"\n--- Relatório concluído! ---"))
            self.stdout.write(f"Planilha salva em: {caminho_completo}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Erro ao salvar o arquivo Excel: {e}"))