# atendimentos/management/commands/sincronizar_rh.py

import os
import re
import pyodbc
from datetime import datetime, date
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from atendimentos.models import Municipe, CategoriaContato, Conta

# --- Importações para a barra de progresso e remoção de acentos ---
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterator, *args, **kwargs):
        return iterator

try:
    from unidecode import unidecode
except ImportError:
    raise CommandError("A biblioteca 'unidecode' não está instalada. Por favor, rode 'pip install unidecode'.")

# --- Funções auxiliares (sem alterações) ---
def formatar_telefone(numero):
    if not numero: return ''
    numeros_limpos = re.sub(r'\D', '', str(numero))
    if len(numeros_limpos) == 11:
        return f"({numeros_limpos[:2]}) {numeros_limpos[2:7]}-{numeros_limpos[7:]}"
    elif len(numeros_limpos) == 10:
        return f"({numeros_limpos[:2]}) {numeros_limpos[2:6]}-{numeros_limpos[6:]}"
    return str(numero)

def formatar_cpf(cpf):
    if not cpf: return None
    numeros = re.sub(r'\D', '', str(cpf))
    if len(numeros) != 11: return None
    return f'{numeros[:3]}.{numeros[3:6]}.{numeros[6:9]}-{numeros[9:]}'


class Command(BaseCommand):
    help = 'Sincroniza os contatos de servidores a partir do banco de dados do RH (RHV00100).'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Iniciando sincronização de servidores do RH...'))

        # --- 1. Conexão com o RH ---
        try:
            conn_str = (
                f"DRIVER={{ODBC Driver 18 for SQL Server}};"
                f"SERVER={os.environ.get('SQLSERVER_HOST')};"
                f"DATABASE={os.environ.get('SQLSERVER_DB')};"
                f"UID={os.environ.get('SQLSERVER_USER')};"
                f"PWD={os.environ.get('SQLSERVER_PASS')};"
                "TrustServerCertificate=yes;"
            )
            self.stdout.write(f"Conectando ao SQL Server em {os.environ.get('SQLSERVER_HOST')}...")
            conn = pyodbc.connect(conn_str, autocommit=True)
            cursor = conn.cursor()
            self.stdout.write(self.style.SUCCESS('Conectado ao banco do RH! Executando consulta...'))
            sql = "SELECT Matricula, Nome_Funcionario, CPF, DtNascto, Email, Celular, Des_cargo, CEP, Endereco, Bairro FROM RHV00100 WHERE DtDesliga IS NULL"
            cursor.execute(sql)
            servidores_rh = cursor.fetchall()
            cursor.close()
            conn.close()
            self.stdout.write(f'{len(servidores_rh)} servidores ativos encontrados no RH.')
        except pyodbc.Error as e:
            raise CommandError(f'Erro de conexão ou consulta ao banco de dados do RH: {e}')

        if not servidores_rh:
            self.stdout.write(self.style.WARNING('Nenhum servidor encontrado. Processo encerrado.'))
            return

        # --- 2. Preparação dos Dados do SIGA ---
        categoria_servidor, _ = CategoriaContato.objects.get_or_create(nome="SERVIDOR(A)")
        categoria_secretario, _ = CategoriaContato.objects.get_or_create(nome="SECRETÁRIO(A) MUNICIPAL")
        gabinete_prefeita, _ = Conta.objects.get_or_create(nome="GABINETE DA PREFEITA")
        gabinete_vice, _ = Conta.objects.get_or_create(nome="VICE-PREFEITO")
        contas_para_vincular = [gabinete_prefeita, gabinete_vice]

        cont_criados, cont_atualizados, erros = 0, 0, []
        matriculas_rh_encontradas = set()

        # --- 3. Lógica de Sincronização ---
        self.stdout.write('Processando registros...')
        for servidor_data in tqdm(servidores_rh, desc="Sincronizando"):
            with transaction.atomic():
                try:
                    matricula = str(servidor_data.Matricula).strip()
                    if not matricula: continue
                    matriculas_rh_encontradas.add(matricula)
                    
                    cpf_formatado = formatar_cpf(servidor_data.CPF)
                    cargo_servidor = str(servidor_data.Des_cargo or '').strip()
                    cargo_normalizado = unidecode(cargo_servidor).lower()
                    
                    categoria_a_ser_usada = categoria_secretario if 'secretari' in cargo_normalizado else categoria_servidor

                    municipe = None
                    if cpf_formatado: municipe = Municipe.objects.filter(cpf=cpf_formatado).first()
                    if not municipe and matricula: municipe = Municipe.objects.filter(matricula_rh=matricula).first()

                    # -----------------------------------------------------------------
                    # --- INÍCIO DA LÓGICA DE ATUALIZAÇÃO AJUSTADA ---
                    # -----------------------------------------------------------------
                    if municipe:
                        # Atualiza os campos que devem ser sempre sobrescritos
                        municipe.nome_completo = str(servidor_data.Nome_Funcionario or '').strip()
                        municipe.data_nascimento = servidor_data.DtNascto if isinstance(servidor_data.DtNascto, (datetime, date)) else None
                        municipe.cargo = cargo_servidor
                        municipe.orgao = 'Prefeitura Municipal de Mogi das Cruzes'
                        municipe.endereco = {'cep': str(servidor_data.CEP or '').strip(), 'logradouro': str(servidor_data.Endereco or '').strip(), 'bairro': str(servidor_data.Bairro or '').strip()}
                        municipe.categoria = categoria_a_ser_usada
                        municipe.ativo = True
                        municipe.matricula_rh = matricula
                        municipe.cpf = cpf_formatado

                        # Lógica aditiva para E-MAILS
                        email_rh = str(servidor_data.Email or '').strip().lower()
                        if email_rh:
                            # Garante que 'emails' seja uma lista, mesmo que esteja nulo no DB
                            if not isinstance(municipe.emails, list):
                                municipe.emails = []
                            
                            # Verifica se o e-mail do RH já existe na lista (ignorando maiúsculas/minúsculas)
                            emails_existentes = {e.get('email', '').lower() for e in municipe.emails if isinstance(e, dict)}
                            if email_rh not in emails_existentes:
                                municipe.emails.append({'tipo': 'corporativo', 'email': email_rh})
                                self.stdout.write(f"  - Adicionado novo e-mail para {municipe.nome_completo}")

                        # Lógica aditiva para TELEFONES
                        telefone_rh = formatar_telefone(servidor_data.Celular)
                        if telefone_rh:
                            # Garante que 'telefones' seja uma lista
                            if not isinstance(municipe.telefones, list):
                                municipe.telefones = []

                            # Verifica se o telefone do RH já existe na lista
                            numeros_existentes = {t.get('numero') for t in municipe.telefones if isinstance(t, dict)}
                            if telefone_rh not in numeros_existentes:
                                municipe.telefones.append({'tipo': 'celular', 'numero': telefone_rh})
                                self.stdout.write(f"  - Adicionado novo telefone para {municipe.nome_completo}")

                        municipe.save()
                        cont_atualizados += 1
                    # -----------------------------------------------------------------
                    # --- FIM DA LÓGICA DE ATUALIZAÇÃO AJUSTADA ---
                    # -----------------------------------------------------------------
                    else:
                        # Se o munícipe não existe, cria um novo com os dados do RH
                        dados_para_criar = {
                            'nome_completo': str(servidor_data.Nome_Funcionario or '').strip(),
                            'data_nascimento': servidor_data.DtNascto if isinstance(servidor_data.DtNascto, (datetime, date)) else None,
                            'emails': [{'tipo': 'corporativo', 'email': str(servidor_data.Email or '').strip().lower()}] if servidor_data.Email else [],
                            'cargo': cargo_servidor,
                            'orgao': 'Prefeitura Municipal de Mogi das Cruzes',
                            'telefones': [{'tipo': 'celular', 'numero': formatar_telefone(servidor_data.Celular)}] if servidor_data.Celular else [],
                            'endereco': {'cep': str(servidor_data.CEP or '').strip(), 'logradouro': str(servidor_data.Endereco or '').strip(), 'bairro': str(servidor_data.Bairro or '').strip()},
                            'categoria': categoria_a_ser_usada,
                            'ativo': True,
                            'matricula_rh': matricula,
                            'cpf': cpf_formatado
                        }
                        municipe = Municipe.objects.create(**dados_para_criar)
                        cont_criados += 1
                    
                    municipe.contas.set(contas_para_vincular)

                except Exception as e:
                    erros.append(f"Erro ao processar matrícula {matricula}: {e}")

        # --- 4. Desativar servidores ---
        self.stdout.write('Verificando servidores para desativar...')
        with transaction.atomic():
            servidores_para_desativar = Municipe.objects.filter(
                categoria__in=[categoria_servidor, categoria_secretario],
                ativo=True
            ).exclude(matricula_rh__in=matriculas_rh_encontradas)
            count_desativados = servidores_para_desativar.update(ativo=False)

        # --- 5. Relatório Final ---
        self.stdout.write(self.style.SUCCESS('-----------------------------------------'))
        self.stdout.write(self.style.SUCCESS('Sincronização concluída!'))
        self.stdout.write(f'Servidores Criados: {cont_criados}')
        self.stdout.write(f'Servidores Atualizados: {cont_atualizados}')
        self.stdout.write(f'Servidores Desativados: {count_desativados}')
        if erros:
            self.stdout.write(self.style.ERROR(f'Ocorreram {len(erros)} avisos/erros durante o processo:'))
            for erro in erros:
                self.stdout.write(self.style.WARNING(f'   - {erro}'))