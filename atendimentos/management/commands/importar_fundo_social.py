import csv
import re
import requests
import time
import os
from urllib.parse import quote
from datetime import datetime
from django.core.management.base import BaseCommand
from django.conf import settings
from atendimentos.models import Municipe, CategoriaContato

class Command(BaseCommand):
    help = 'Importa Entidades (CSV) e gera relatório de CEPs não encontrados.'

    def add_arguments(self, parser):
        parser.add_argument('caminho_do_arquivo', type=str, help='Caminho do CSV.')

    def extrair_telefones(self, texto_bruto):
        if not texto_bruto: return []
        padrao = r'\(?\d{2}\)?\s*\d{4,5}[-\s]*\d{4}'
        encontrados = re.findall(padrao, str(texto_bruto))
        numeros_limpos = []
        for item in encontrados:
            somente_digitos = re.sub(r'\D', '', item)
            if len(somente_digitos) == 11:
                fmt = f"({somente_digitos[:2]}) {somente_digitos[2:7]}-{somente_digitos[7:]}"
                numeros_limpos.append(fmt)
            elif len(somente_digitos) == 10:
                fmt = f"({somente_digitos[:2]}) {somente_digitos[2:6]}-{somente_digitos[6:]}"
                numeros_limpos.append(fmt)
        return numeros_limpos

    def title_case(self, text):
        if not text: return ""
        text = str(text).strip()
        exceptions = ['de', 'da', 'do', 'dos', 'das', 'e', 'em']
        words = text.lower().split()
        new_words = [w if w in exceptions and i != 0 else w.capitalize() for i, w in enumerate(words)]
        return " ".join(new_words)

    def extrair_nome_responsavel(self, texto_responsavel):
        if not texto_responsavel: return ""
        primeiro = texto_responsavel.split(';')[0].strip()
        nome_limpo = re.sub(r'\s*\(.*?\)', '', primeiro)
        return self.title_case(nome_limpo)

    def buscar_cep_na_api(self, logradouro, bairro_alvo):
        logradouro_clean = re.sub(r'[^\w\s]', '', logradouro).strip()
        if len(logradouro_clean) < 3: return ""

        uf = "SP"
        cidade = "Mogi das Cruzes"
        url = f"https://viacep.com.br/ws/{uf}/{quote(cidade)}/{quote(logradouro_clean)}/json/"
        
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                dados = response.json()
                if isinstance(dados, list) and len(dados) > 0:
                    for item in dados:
                        bairro_api = item.get('bairro', '').lower()
                        bairro_csv = bairro_alvo.lower()
                        if bairro_csv and (bairro_api in bairro_csv or bairro_csv in bairro_api):
                            return item.get('cep', '').replace('-', '')
                    if len(dados) == 1:
                        return dados[0].get('cep', '').replace('-', '')
            return ""
        except Exception:
            return ""

    def handle(self, *args, **kwargs):
        caminho = kwargs['caminho_do_arquivo']
        self.stdout.write(f"Lendo: {caminho}")

        categoria, _ = CategoriaContato.objects.get_or_create(nome='ENTIDADES FUNDO SOCIAL')
        cidade_padrao = "Mogi das Cruzes"
        uf_padrao = "SP"

        # Lista para armazenar os erros
        lista_sem_cep = []

        with open(caminho, mode='r', encoding='utf-8-sig') as file: 
            try:
                sample = file.read(2048) 
                file.seek(0)
                dialect = csv.Sniffer().sniff(sample)
            except csv.Error:
                file.seek(0)
                dialect = csv.excel
                dialect.delimiter = ',' 
            
            reader = csv.DictReader(file, dialect=dialect)
            if reader.fieldnames:
                reader.fieldnames = [name.strip() for name in reader.fieldnames]
            
            count = 0
            ceps_recuperados = 0

            for row in reader:
                id_origem = row.get('id', '')
                razao = row.get('razao_social', '').strip()
                fantasia = row.get('nome_fantasia', '').strip()
                nome_entidade = fantasia if fantasia else razao
                
                if not nome_entidade: 
                    resp = row.get('Responsaveis_Vinculados', '')
                    if not resp: continue
                
                documento = row.get('documento', '').strip()
                email_raw = row.get('Email_Principal') or row.get('email') or ''
                telefone_raw = row.get('Telefone_Principal') or row.get('telefone') or ''
                
                logradouro = row.get('logradouro', '').strip()
                numero = row.get('numero', '').strip()
                bairro = row.get('bairro', '').strip()
                cep = row.get('cep', '').strip()
                
                responsaveis_texto = row.get('Responsaveis_Vinculados', '').strip()

                # --- BUSCA INTELIGENTE DE CEP ---
                if not cep and logradouro:
                    # Mensagem detalhada no terminal
                    msg_busca = f"Buscando CEP: {nome_entidade[:30]}... ({logradouro})"
                    self.stdout.write(msg_busca, ending='')
                    
                    novo_cep = self.buscar_cep_na_api(logradouro, bairro)
                    
                    if novo_cep:
                        cep = novo_cep
                        ceps_recuperados += 1
                        self.stdout.write(self.style.SUCCESS(f" ACHOU: {cep}"))
                        time.sleep(0.5)
                    else:
                        self.stdout.write(self.style.WARNING(" [NÃO ENCONTRADO]"))
                        # Adiciona ao relatório de erros
                        lista_sem_cep.append({
                            'id': id_origem,
                            'nome': nome_entidade,
                            'logradouro': logradouro,
                            'numero': numero,
                            'bairro': bairro,
                            'cidade': cidade_padrao
                        })
                elif not cep and not logradouro:
                     # Caso nem tenha endereço preenchido
                     lista_sem_cep.append({
                            'id': id_origem,
                            'nome': nome_entidade,
                            'logradouro': 'SEM ENDEREÇO',
                            'numero': '',
                            'bairro': '',
                            'cidade': ''
                        })
                # -------------------------------

                # ... (Lógica de salvamento mantém igual) ...
                nome_responsavel_etiqueta = self.extrair_nome_responsavel(responsaveis_texto)
                
                lista_telefones = []
                for fone_fmt in self.extrair_telefones(telefone_raw):
                    lista_telefones.append({'tipo': 'comercial', 'numero': fone_fmt})

                lista_emails = []
                if email_raw:
                    for em in email_raw.replace(',', ' ').replace(';', ' ').split():
                        if '@' in em:
                             lista_emails.append({'tipo': 'institucional', 'email': em.strip()})

                obs_parts = []
                if documento: obs_parts.append(f"CNPJ/CPF: {documento}")
                if responsaveis_texto: obs_parts.append(f"Responsáveis: {responsaveis_texto}")
                observacoes_final = " | ".join(obs_parts)

                etiqueta_linhas = []
                etiqueta_linhas.append(nome_entidade.upper())
                if nome_responsavel_etiqueta:
                    etiqueta_linhas.append(f"A/C {nome_responsavel_etiqueta}")
                if logradouro:
                    etiqueta_linhas.append(f"{self.title_case(logradouro)}, {numero}")
                
                partes_cidade = []
                if bairro: partes_cidade.append(self.title_case(bairro))
                if cep: partes_cidade.append(f"CEP {cep}")
                partes_cidade.append(f"{cidade_padrao} {uf_padrao}")
                etiqueta_linhas.append(" - ".join(partes_cidade))
                texto_etiqueta = "\n".join(etiqueta_linhas)

                defaults = {
                    'categoria': categoria,
                    'emails': lista_emails,
                    'telefones': lista_telefones,
                    'observacoes': observacoes_final,
                    'dados_etiqueta': texto_etiqueta,
                    'cargo': 'ENTIDADE',
                    'endereco': {
                        'logradouro': logradouro.upper(),
                        'numero': numero,
                        'bairro': bairro.upper(),
                        'cidade': cidade_padrao.upper(),
                        'uf': uf_padrao,
                        'cep': cep 
                    }
                }

                try:
                    registros_existentes = Municipe.objects.filter(nome_completo=nome_entidade)
                    if registros_existentes.count() > 1:
                        obj = registros_existentes.first()
                        for duplicata in registros_existentes[1:]:
                            duplicata.delete()
                    elif registros_existentes.exists():
                        obj = registros_existentes.first()
                    else:
                        obj = Municipe(nome_completo=nome_entidade)

                    for key, value in defaults.items():
                        setattr(obj, key, value)
                    obj.save()
                    count += 1

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Erro ao salvar '{nome_entidade}': {e}"))

        # --- GERAÇÃO DO RELATÓRIO FINAL ---
        if lista_sem_cep:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            nome_arquivo_erro = f"ceps_nao_encontrados_{timestamp}.csv"
            caminho_erro = os.path.join(settings.MEDIA_ROOT, 'exports', nome_arquivo_erro)
            
            # Garante que a pasta existe
            os.makedirs(os.path.dirname(caminho_erro), exist_ok=True)

            with open(caminho_erro, 'w', newline='', encoding='utf-8-sig') as csvfile:
                fieldnames = ['id', 'nome', 'logradouro', 'numero', 'bairro', 'cidade']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter=';')
                
                writer.writeheader()
                for erro in lista_sem_cep:
                    writer.writerow(erro)
            
            self.stdout.write(self.style.WARNING(f"\n[ATENÇÃO] {len(lista_sem_cep)} registros ficaram sem CEP."))
            self.stdout.write(self.style.WARNING(f"Relatório gerado em: {caminho_erro}"))
            self.stdout.write("Baixe este arquivo, pesquise os CEPs e atualize o CSV original ou o sistema.")
        
        self.stdout.write(self.style.SUCCESS(f"\nImportação Finalizada! {count} processados. {ceps_recuperados} CEPs recuperados via API."))