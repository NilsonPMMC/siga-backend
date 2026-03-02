import csv
import re
import html
from django.core.management.base import BaseCommand
from atendimentos.models import Municipe, CategoriaContato, Conta, PerfilMunicipe

class Command(BaseCommand):
    help = 'Importa Entidades/Escolas do CSV e gera etiquetas.'

    def add_arguments(self, parser):
        parser.add_argument('caminho_do_arquivo', type=str, help='Caminho do CSV.')

    def limpar_html(self, texto):
        """Converte &ccedil; para ç, &atilde; para ã, etc."""
        if not texto: return ""
        return html.unescape(texto).strip()

    def formatar_telefone(self, numero):
        """Formata para (11) 99999-9999"""
        if not numero: return ''
        nums = re.sub(r'\D', '', numero)
        if len(nums) == 11:
            return f"({nums[:2]}) {nums[2:7]}-{nums[7:]}"
        elif len(nums) == 10:
            return f"({nums[:2]}) {nums[2:6]}-{nums[6:]}"
        return numero

    def separar_endereco(self, texto_endereco):
        """Separa 'RUA X, 123' em logradouro e número"""
        texto = self.limpar_html(texto_endereco)
        if ',' in texto:
            partes = texto.rsplit(',', 1)
            return partes[0].strip(), partes[1].strip()
        # Tenta achar numero no fim se não tiver virgula
        match = re.search(r'(\s)(\d+[a-zA-Z]?)$', texto)
        if match:
            numero = match.group(2)
            logradouro = texto[:match.start(1)]
            return logradouro.strip(), numero
        return texto, "S/N"

    def title_case(self, text):
        """Formata texto para Title Case (primeiras maiúsculas)"""
        if not text: return ""
        exceptions = ['de', 'da', 'do', 'dos', 'das', 'e', 'em']
        words = text.lower().split()
        new_words = [w if w in exceptions and i != 0 else w.capitalize() for i, w in enumerate(words)]
        return " ".join(new_words)

    def handle(self, *args, **kwargs):
        caminho = kwargs['caminho_do_arquivo']
        self.stdout.write(f"Lendo: {caminho}")

        # 1. Cria ou Pega a Categoria Específica e conta padrão
        categoria, _ = CategoriaContato.objects.get_or_create(nome='ENTIDADES SUBVENCIONADAS')
        conta_padrao, _ = Conta.objects.get_or_create(nome='GABINETE DA PREFEITA', defaults={'nome_instituicao': 'Prefeitura Municipal'})
        
        cidade_padrao = "Mogi das Cruzes"
        uf_padrao = "SP"

        # Tente utf-8-sig ou latin-1 conforme seu arquivo
        with open(caminho, mode='r', encoding='utf-8-sig') as file: 
            
            reader = csv.DictReader(file, delimiter=';')
            
            # Normalizando nomes das colunas
            novos_headers = [html.unescape(h).strip() for h in reader.fieldnames]
            reader.fieldnames = novos_headers

            count = 0
            for row in reader:
                nome_entidade = row.get('Denominação', '').strip()
                if not nome_entidade: continue

                endereco_raw = row.get('Endereço', '')
                bairro = row.get('Bairro', '').strip()
                email = row.get('E-mail', '').strip()
                supervisor = row.get('Supervisor', '').strip() 
                telefones_raw = row.get('Telefone', '').strip()

                # Processamento Endereço
                logradouro, numero = self.separar_endereco(endereco_raw)
                
                # Tratamento de múltiplos telefones
                lista_telefones = []
                for fone in telefones_raw.split():
                    f_fmt = self.formatar_telefone(fone)
                    if f_fmt:
                        lista_telefones.append({'tipo': 'institucional', 'numero': f_fmt})

                # Tratamento do Email (CORREÇÃO AQUI)
                lista_emails = []
                if email:
                    # Pode haver múltiplos emails separados por espaço ou vírgula, vamos garantir
                    for em in email.replace(',', ' ').split():
                        if '@' in em: # Validação básica
                             lista_emails.append({'tipo': 'institucional', 'email': em})

                # GERAÇÃO DA ETIQUETA
                etiqueta_linhas = []
                etiqueta_linhas.append(nome_entidade.upper())
                
                if supervisor:
                    etiqueta_linhas.append(f"A/C {self.title_case(supervisor)}")
                
                linha_end = f"{self.title_case(logradouro)}, {numero}"
                etiqueta_linhas.append(linha_end)
                
                linha_cidade = f"{self.title_case(bairro)} - {cidade_padrao} {uf_padrao}"
                etiqueta_linhas.append(linha_cidade)

                texto_etiqueta = "\n".join(etiqueta_linhas)

                # Criação/Atualização no Banco
                defaults = {
                    # 'email': email,  <-- CAMPO REMOVIDO
                    'emails': lista_emails, # <-- NOVO CAMPO JSON
                    'telefones': lista_telefones,
                    'observacoes': f"Supervisor(a): {supervisor}" if supervisor else "",
                    'dados_etiqueta': texto_etiqueta,
                    'endereco': {
                        'logradouro': logradouro.upper(),
                        'numero': numero,
                        'bairro': bairro.upper(),
                        'cidade': cidade_padrao.upper(),
                        'uf': uf_padrao,
                        'cep': '' 
                    }
                }

                obj, created = Municipe.objects.update_or_create(
                    nome_completo=nome_entidade, 
                    defaults=defaults
                )
                obj.contas.add(conta_padrao)
                PerfilMunicipe.objects.update_or_create(
                    municipe=obj, conta=conta_padrao,
                    defaults={'categoria': categoria, 'cargo': None, 'instituicao': None, 'ativo': True}
                )
                
                status = "CRIADO" if created else "ATUALIZADO"
                self.stdout.write(f"{status}: {nome_entidade}")
                count += 1

        self.stdout.write(self.style.SUCCESS(f"Importação Finalizada! {count} entidades processadas."))