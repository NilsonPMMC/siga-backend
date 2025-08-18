import csv
import re
from datetime import datetime
from django.core.management.base import BaseCommand
from atendimentos.models import Municipe, Conta, CategoriaContato

def formatar_telefone(numero):
    """Limpa e formata um número de telefone para o padrão (99) 99999-9999."""
    if not numero:
        return ''

    # Remove tudo que não for dígito
    numeros_limpos = re.sub(r'\D', '', numero)

    if len(numeros_limpos) == 11: # Celular com DDD
        return f"({numeros_limpos[:2]}) {numeros_limpos[2:7]}-{numeros_limpos[7:]}"
    elif len(numeros_limpos) == 10: # Fixo com DDD
        return f"({numeros_limpos[:2]}) {numeros_limpos[2:6]}-{numeros_limpos[6:]}"
    else:
        return numero # Retorna o número original se não se encaixar nos padrões

class Command(BaseCommand):
    help = 'Importa munícipes a partir de um arquivo CSV.'

    def add_arguments(self, parser):
        parser.add_argument('caminho_do_arquivo', type=str, help='O caminho completo para o arquivo CSV.')

    def handle(self, *args, **kwargs):
        caminho_do_arquivo = kwargs['caminho_do_arquivo']
        self.stdout.write(self.style.SUCCESS(f"Iniciando a importação de: {caminho_do_arquivo}"))

        categoria_padrao, _ = CategoriaContato.objects.get_or_create(nome='Munícipe')

        try:
            with open(caminho_do_arquivo, mode='r', encoding='latin-1') as file:
                reader = csv.DictReader(file, delimiter=';')

                total_linhas = 0
                importados_sucesso = 0
                erros = []

                for row in reader:
                    total_linhas += 1
                    try:
                        categoria = categoria_padrao
                        nome_categoria = row.get('categoria', '').strip()
                        if nome_categoria:
                            # Busca ou cria a categoria da planilha
                            categoria, _ = CategoriaContato.objects.get_or_create(nome=nome_categoria)
                        # Tenta encontrar o gabinete pelo nome
                        conta_proprietaria = None
                        nome_gabinete = row.get('gabinete_proprietario', '').strip()
                        if nome_gabinete:
                            conta_proprietaria = Conta.objects.get(nome__iexact=nome_gabinete)

                        telefone_raw = row.get('telefone', '').strip()
                        telefone_formatado = formatar_telefone(telefone_raw)

                        data_nasc_obj = None
                        data_nasc_str = row.get('data_nascimento', '').strip()
                        if data_nasc_str:
                            try:
                                # Tenta converter 'dd/mm/aaaa' para um objeto de data do Python
                                data_nasc_obj = datetime.strptime(data_nasc_str, '%d/%m/%Y').date()
                            except ValueError:
                                self.stdout.write(self.style.WARNING(f"  - Linha {total_linhas}: Formato de data inválido para '{data_nasc_str}'. Deixando em branco."))

                        # Cria ou atualiza o munícipe baseado no CPF (se existir) ou nome completo
                        cpf = row.get('cpf', '').strip()
                        if cpf:
                            municipe, created = Municipe.objects.update_or_create(
                                cpf=cpf,
                                defaults={
                                    'nome_completo': row['nome_completo'],
                                    'data_nascimento': data_nasc_obj,
                                    'email': row.get('email', ''),
                                    'cargo': row.get('cargo', ''),
                                    'orgao': row.get('orgao', ''),
                                    'conta_proprietaria': conta_proprietaria,
                                    'categoria': categoria,
                                    'telefones': [{'tipo': 'principal', 'numero': telefone_formatado}] if telefone_formatado else []
                                }
                            )
                        else:
                            # Se não houver CPF, usamos o nome completo como chave
                            municipe, created = Municipe.objects.update_or_create(
                                nome_completo=row['nome_completo'],
                                defaults={
                                    'data_nascimento': data_nasc_obj,
                                    'email': row.get('email', ''),
                                    'cargo': row.get('cargo', ''),
                                    'orgao': row.get('orgao', ''),
                                    'conta_proprietaria': conta_proprietaria,
                                    'categoria': categoria,
                                    'telefones': [{'tipo': 'principal', 'numero': telefone_formatado}] if telefone_formatado else []
                                }
                            )

                        importados_sucesso += 1
                        if created:
                            self.stdout.write(f"  - Munícipe CRIADO: {municipe.nome_completo}")
                        else:
                            self.stdout.write(f"  - Munícipe ATUALIZADO: {municipe.nome_completo}")

                    except Conta.DoesNotExist:
                        erro = f"Linha {total_linhas}: Gabinete '{nome_gabinete}' não encontrado. Munícipe '{row['nome_completo']}' não importado."
                        self.stdout.write(self.style.ERROR(erro))
                        erros.append(erro)
                    except Exception as e:
                        erro = f"Linha {total_linhas}: Erro ao importar munícipe '{row['nome_completo']}'. Erro: {e}"
                        self.stdout.write(self.style.ERROR(erro))
                        erros.append(erro)

            self.stdout.write(self.style.SUCCESS('-----------------------------------------'))
            self.stdout.write(self.style.SUCCESS(f"Importação Concluída! Total de linhas processadas: {total_linhas}"))
            self.stdout.write(self.style.SUCCESS(f"Munícipes importados/atualizados com sucesso: {importados_sucesso}"))
            if erros:
                self.stdout.write(self.style.ERROR(f"Total de erros: {len(erros)}"))

        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f"Erro: O arquivo '{caminho_do_arquivo}' não foi encontrado."))
