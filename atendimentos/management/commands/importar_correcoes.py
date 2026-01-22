import csv
import os
from django.core.management.base import BaseCommand
from django.conf import settings
from atendimentos.models import Municipe

class Command(BaseCommand):
    help = 'Importa correções de cadastro via CSV e gera o texto da etiqueta (Nome UPPER, resto Title).'

    def add_arguments(self, parser):
        parser.add_argument('arquivo_csv', type=str, help='Nome do arquivo dentro da pasta media/exports (ex: correcao.csv)')

    def smart_title(self, text):
        """
        Converte para Title Case (Iniciais maiúsculas), mantendo preposições minúsculas.
        Ex: 'RUA DA PAZ' -> 'Rua da Paz'
        """
        if not text: return ""
        text = str(text).strip()
        exceptions = ['de', 'da', 'do', 'dos', 'das', 'e', 'em', 'para', 'com']
        
        words = text.lower().split()
        new_words = []
        for i, word in enumerate(words):
            # Mantém exceções em minúsculo, exceto se for a primeira palavra
            if word in exceptions and i != 0:
                new_words.append(word)
            else:
                new_words.append(word.capitalize())
        
        return " ".join(new_words)

    def handle(self, *args, **options):
        filename = options['arquivo_csv']
        file_path = os.path.join(settings.MEDIA_ROOT, 'exports', filename)

        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f"Arquivo não encontrado: {file_path}"))
            return

        self.stdout.write(f"Lendo arquivo: {file_path}...")

        updated_count = 0
        
        with open(file_path, 'r', encoding='utf-8-sig') as csvfile:
            reader = csv.DictReader(csvfile, delimiter=';')
            
            for row in reader:
                try:
                    m_id = row.get('ID (Não alterar)')
                    if not m_id: continue

                    municipe = Municipe.objects.get(pk=m_id)
                    
                    # 1. Recupera e Limpa os Dados do CSV
                    tratamento = row.get('Tratamento Atual', '').strip()
                    
                    # AQUI: Lógica para Logradouro e Número unidos
                    # Se vier "Rua X, 100", tentamos separar.
                    raw_logradouro = row.get('Logradouro', '').strip()
                    numero = row.get('Número', '').strip() # Pode estar vazio se o usuário uniu tudo na col anterior
                    
                    if ',' in raw_logradouro and not numero:
                        # Tenta separar automaticamente pela última vírgula
                        partes = raw_logradouro.rsplit(',', 1)
                        logradouro_final = partes[0].strip()
                        numero_final = partes[1].strip()
                    else:
                        logradouro_final = raw_logradouro
                        numero_final = numero if numero else "S/N"

                    bairro = row.get('Bairro', '').strip()
                    cep = row.get('CEP', '').strip()
                    cidade = row.get('Cidade', '').strip()
                    uf = row.get('UF', '').strip()

                    # 2. Atualiza o cadastro do Municipe (No banco fica tudo organizado)
                    municipe.tratamento = tratamento
                    
                    # Atualiza JSON de endereço
                    endereco_dict = municipe.endereco if isinstance(municipe.endereco, dict) else {}
                    endereco_dict.update({
                        'logradouro': logradouro_final.upper(), # No banco salvamos UPPER por padrão do sistema
                        'numero': numero_final,
                        'bairro': bairro.upper(),
                        'cep': cep,
                        'cidade': cidade.upper(),
                        'uf': uf.upper()
                    })
                    municipe.endereco = endereco_dict
                    
                    # 3. GERAÇÃO DO TEXTO DA ETIQUETA (A Mágica da Formatação)
                    
                    # Nome: Sempre MAIÚSCULO (Padrão do model)
                    nome_formatado = municipe.nome_completo.upper()
                    
                    # Tratamento: Title Case (Ex: A Sua Excelência o Senhor)
                    tratamento_formatado = self.smart_title(tratamento)
                    
                    # Cargo: Title Case
                    cargo_formatado = self.smart_title(municipe.cargo)
                    
                    # Endereço: Title Case
                    logradouro_label = self.smart_title(logradouro_final)
                    bairro_label = self.smart_title(bairro)
                    cidade_label = self.smart_title(cidade)
                    uf_label = uf.upper() # UF sempre maiúsculo
                    
                    linhas_etiqueta = []
                    if tratamento_formatado: linhas_etiqueta.append(tratamento_formatado)
                    linhas_etiqueta.append(nome_formatado)
                    if cargo_formatado: linhas_etiqueta.append(cargo_formatado)
                    
                    # Linha do endereço: Rua Xyz, 123
                    linha_rua = f"{logradouro_label}, {numero_final}"
                    linhas_etiqueta.append(linha_rua)
                    
                    # Linha final: Bairro - CEP - Cidade UF
                    partes_final = []
                    if bairro_label: partes_final.append(bairro_label)
                    if cep: partes_final.append(cep)
                    
                    cidade_completa = f"{cidade_label} {uf_label}" if uf_label else cidade_label
                    if cidade_completa: partes_final.append(cidade_completa)
                    
                    linhas_etiqueta.append(" - ".join(partes_final))

                    # Salva no campo texto
                    municipe.dados_etiqueta = "\n".join(linhas_etiqueta)
                    
                    municipe.save()
                    updated_count += 1

                except Municipe.DoesNotExist:
                    self.stdout.write(self.style.WARNING(f"ID {m_id} não encontrado."))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Erro na linha {row}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Processo finalizado! {updated_count} cadastros atualizados e etiquetas geradas."))