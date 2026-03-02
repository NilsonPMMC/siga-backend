import csv
import os
from datetime import datetime
from django.core.management.base import BaseCommand
from django.conf import settings
from atendimentos.models import Municipe

class Command(BaseCommand):
    help = 'Exporta uma planilha (CSV) com os cadastros incompletos para correção manual.'

    def handle(self, *args, **options):
        # Categorias alvo
        TARGET_IDS = [30, 34, 21, 19]
        
        # Cria o diretório de exports se não existir
        export_dir = os.path.join(settings.MEDIA_ROOT, 'exports')
        os.makedirs(export_dir, exist_ok=True)
        
        # Nome do arquivo com data/hora
        filename = f"correcao_cadastro_etiquetas_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        file_path = os.path.join(export_dir, filename)

        self.stdout.write(f"Gerando relatório em: {file_path} ...")

        with open(file_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
            # Usamos ponto-e-vírgula (;) pois o Excel em português prefere assim
            writer = csv.writer(csvfile, delimiter=';')
            
            # Cabeçalho da Planilha
            writer.writerow([
                'ID (Não alterar)', 
                'Nome Completo', 
                'Categoria', 
                'SITUAÇÃO', 
                'DADOS FALTANTES',
                'Tratamento Atual',
                'Logradouro',
                'Número',
                'Bairro',
                'CEP',
                'Cidade',
                'UF'
            ])

            municipes = Municipe.objects.filter(perfis__categoria__id__in=TARGET_IDS).prefetch_related('perfis__categoria').distinct()
            
            count_incompletos = 0

            for m in municipes:
                # Extrai dados atuais
                end = m.endereco if isinstance(m.endereco, dict) else {}
                
                tratamento = m.tratamento or ""
                logradouro = end.get('logradouro', '').strip()
                numero = end.get('numero', '').strip()
                bairro = end.get('bairro', '').strip()
                cep = end.get('cep', '').strip()
                cidade = end.get('cidade', '').strip()
                uf = end.get('uf', '').strip()

                # Verifica o que falta
                faltantes = []
                if not tratamento: faltantes.append("Tratamento")
                if not logradouro: faltantes.append("Logradouro")
                if not numero: faltantes.append("Número")
                if not cep: faltantes.append("CEP")
                if not cidade: faltantes.append("Cidade")

                # Se tiver algo faltando, adiciona na planilha
                if faltantes:
                    count_incompletos += 1
                    status = "INCOMPLETO"
                    lista_faltantes = ", ".join(faltantes)
                    
                    writer.writerow([
                        m.id,
                        m.nome_completo,
                        ', '.join(sorted({p.categoria.nome for p in m.perfis.all() if p.categoria})) or 'Sem Categoria',
                        status,
                        lista_faltantes, # Coluna fácil para ver o que precisa
                        tratamento,
                        logradouro,
                        numero,
                        bairro,
                        cep,
                        cidade,
                        uf
                    ])

        self.stdout.write(self.style.SUCCESS(f"\nArquivo gerado com sucesso!"))
        self.stdout.write(f"Total de registros para corrigir: {count_incompletos}")
        self.stdout.write(f"Local: {file_path}")
        self.stdout.write("Agora você pode baixar esse arquivo via SFTP ou disponibilizar um link para download.")