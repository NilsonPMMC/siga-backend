import os
import re
import pyodbc
from datetime import datetime, date
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, utils
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

        # --- 1. Conexão com o RH (COM A CORREÇÃO DEFINITIVA) ---
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
            
            # Conecta usando a string de conexão e remove as configurações manuais de encoding
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

        # --- O resto do script permanece exatamente o mesmo, pois a lógica está correta ---
        
        # --- 2. Preparação dos Dados do SIGA ---
        categoria_servidor, _ = CategoriaContato.objects.get_or_create(nome="Servidor(a)")
        categoria_secretario, _ = CategoriaContato.objects.get_or_create(nome="Secretário(a) Municipal")
        gabinete_prefeita, _ = Conta.objects.get_or_create(nome="Gabinete Prefeita")
        gabinete_vice, _ = Conta.objects.get_or_create(nome="Gabinete Vice-Prefeito")
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
                    
                    if 'secretari' in cargo_normalizado:
                        categoria_a_ser_usada = categoria_secretario
                    else:
                        categoria_a_ser_usada = categoria_servidor

                    dados_para_salvar = {
                        'nome_completo': str(servidor_data.Nome_Funcionario or '').strip(),
                        'data_nascimento': servidor_data.DtNascto if isinstance(servidor_data.DtNascto, (datetime, date)) else None,
                        'email': str(servidor_data.Email or '').strip().lower(),
                        'cargo': cargo_servidor,
                        'orgao': 'Prefeitura Municipal de Mogi das Cruzes',
                        'telefones': [{'tipo': 'principal', 'numero': formatar_telefone(servidor_data.Celular)}] if servidor_data.Celular else [],
                        'endereco': {'cep': str(servidor_data.CEP or '').strip(), 'logradouro': str(servidor_data.Endereco or '').strip(), 'bairro': str(servidor_data.Bairro or '').strip()},
                        'categoria': categoria_a_ser_usada,
                        'ativo': True,
                        'matricula_rh': matricula,
                        'cpf': cpf_formatado
                    }

                    municipe = None
                    if cpf_formatado: municipe = Municipe.objects.filter(cpf=cpf_formatado).first()
                    if not municipe and matricula: municipe = Municipe.objects.filter(matricula_rh=matricula).first()

                    if municipe:
                        for key, value in dados_para_salvar.items():
                            setattr(municipe, key, value)
                        municipe.save()
                        cont_atualizados += 1
                    else:
                        municipe = Municipe.objects.create(**dados_para_salvar)
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
                self.stdout.write(self.style.WARNING(f'  - {erro}'))