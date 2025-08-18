# atendimentos/management/commands/importar_emails.py

import re
import json
from django.core.management.base import BaseCommand
from atendimentos.models import Municipe

class Command(BaseCommand):
    help = 'Importa os e-mails antigos do backup para o novo formato JSONField.'

    def add_arguments(self, parser):
        parser.add_argument('caminho_do_arquivo', type=str, help='O caminho para o arquivo SQL com os dados dos munícipes.')

    def handle(self, *args, **options):
        caminho_arquivo = options['caminho_do_arquivo']
        self.stdout.write(self.style.SUCCESS(f"Iniciando a importação do arquivo: {caminho_arquivo}"))

        try:
            with open(caminho_arquivo, 'r', encoding='utf-8') as f:
                conteudo = f.read()
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR("ERRO: Arquivo não encontrado. Verifique o caminho."))
            return

        # Regex para encontrar os valores dentro de cada INSERT
        # Ex: (1, 'Nome Completo', 'cpf', ..., 'email@dominio.com', ...)
        padrao_insert = re.compile(r"\((.*?)\)")

        # Encontra todos os conjuntos de valores no arquivo
        inserts = padrao_insert.findall(conteudo)

        total_encontrado = len(inserts)
        atualizados = 0

        self.stdout.write(f"{total_encontrado} registros encontrados no arquivo de backup.")

        for insert in inserts:
            try:
                # Divide os valores pela vírgula, mas trata strings com vírgulas dentro
                valores = [v.strip().strip("'") for v in insert.split(',')]

                # Com base no seu modelo Municipe, o ID é o 1º valor e o email é o 5º
                municipe_id = int(valores[0])
                email_antigo = valores[4]

                if email_antigo and email_antigo != 'NULL' and '@' in email_antigo:
                    # Tenta encontrar o munícipe no banco de dados atual
                    municipe = Municipe.objects.get(id=municipe_id)

                    # Cria a nova estrutura JSON
                    novo_formato_emails = [{
                        "tipo": "principal",
                        "email": email_antigo
                    }]

                    # Atualiza o campo 'emails' e salva
                    municipe.emails = novo_formato_emails
                    municipe.save()

                    atualizados += 1
                    if atualizados % 100 == 0:
                        self.stdout.write(f"... {atualizados} e-mails atualizados.")

            except (IndexError, ValueError, Municipe.DoesNotExist) as e:
                # Ignora linhas que não são de dados ou que têm IDs que não existem mais
                pass

        self.stdout.write(self.style.SUCCESS(f"\nPROCESSO CONCLUÍDO! {atualizados} de {total_encontrado} contatos tiveram seus e-mails restaurados."))