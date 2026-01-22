import json
from django.core.management.base import BaseCommand
from atendimentos.models import Municipe, CategoriaContato

class Command(BaseCommand):
    help = 'Gera o texto da etiqueta para categorias específicas e faz diagnóstico de dados faltantes.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Realmente salva as alterações no banco de dados. Sem isso, apenas simula (diagnóstico).'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Sobrescreve etiquetas que já possuem texto. O padrão é pular as preenchidas.'
        )

    def handle(self, *args, **options):
        # IDs solicitados
        TARGET_IDS = [30, 34, 21, 19]
        
        # Mapeamento de nomes para o relatório
        CAT_NAMES = {
            30: 'IGREJAS CATÓLICAS',
            34: 'IGREJAS EVANGÉLICAS',
            21: 'PASTORES',
            19: 'JUDICIÁRIO'
        }

        self.stdout.write(self.style.WARNING(f"--- INICIANDO DIAGNÓSTICO PARA CATEGORIAS: {TARGET_IDS} ---"))

        municipes = Municipe.objects.filter(categoria__id__in=TARGET_IDS)
        
        total = municipes.count()
        validos_para_gerar = 0
        com_erros = 0
        ignorados_ja_preenchidos = 0

        erros_detalhes = []

        for m in municipes:
            # 1. Checa se já tem etiqueta (e se não forçamos a sobreescrita)
            if m.dados_etiqueta and not options['force']:
                ignorados_ja_preenchidos += 1
                continue

            # 2. Extrai dados
            tratamento = m.tratamento or ""
            nome = m.nome_completo
            cargo = m.cargo or ""
            
            # Dados do endereço JSON (com safe get para evitar crash)
            end = m.endereco if isinstance(m.endereco, dict) else {}
            logradouro = end.get('logradouro', '').strip()
            numero = end.get('numero', '').strip()
            bairro = end.get('bairro', '').strip()
            cep = end.get('cep', '').strip()
            cidade = end.get('cidade', '').strip()
            uf = end.get('uf', '').strip()

            # 3. Validação de Integridade (Diagnóstico)
            problemas = []
            if not tratamento:
                problemas.append("Falta Tratamento")
            # Nome é obrigatório no model, então deve existir
            # Cargo não é obrigatório para etiqueta, mas é bom avisar se for o padrão
            
            # Validação crítica de endereço
            if not logradouro: problemas.append("Falta Logradouro")
            if not numero: problemas.append("Falta Número")
            if not cep: problemas.append("Falta CEP")
            if not cidade: problemas.append("Falta Cidade")

            if problemas:
                com_erros += 1
                cat_nome = CAT_NAMES.get(m.categoria_id, 'Outros')
                erros_detalhes.append(f"ID {m.id} | {nome} ({cat_nome}): {', '.join(problemas)}")
                continue # Pula a geração se faltar dados críticos de endereço

            # 4. Monta o texto no padrão solicitado
            # Padrão:
            # <tratamento>
            # <nome_completo>
            # <cargo>
            # <endereço>
            
            linhas = []
            if tratamento: linhas.append(tratamento)
            linhas.append(nome)
            if cargo: linhas.append(cargo)
            
            # Formatação do endereço: "Rua X, 123"
            linha_end1 = f"{logradouro}, {numero}"
            linhas.append(linha_end1)
            
            # Formatação final: "Bairro - CEP - Cidade UF"
            partes_end2 = [p for p in [bairro, cep] if p]
            # Adiciona cidade/uf no final
            cidade_uf = f"{cidade} {uf}" if uf else cidade
            if cidade_uf: partes_end2.append(cidade_uf)
            
            linha_end2 = " - ".join(partes_end2)
            linhas.append(linha_end2)

            novo_texto = "\n".join(linhas)

            validos_para_gerar += 1

            # 5. Ação (Commit ou Preview)
            if options['commit']:
                m.dados_etiqueta = novo_texto
                m.save()
                if validos_para_gerar % 10 == 0:
                    self.stdout.write(f"Processado: {m.id} - {nome}")

        # --- RELATÓRIO FINAL ---
        self.stdout.write("\n" + "="*40)
        self.stdout.write(f"TOTAL ANALISADO: {total}")
        self.stdout.write(f"PRONTOS PARA ATUALIZAR: {validos_para_gerar}")
        self.stdout.write(f"JÁ TINHAM ETIQUETA (IGNORADOS): {ignorados_ja_preenchidos}")
        self.stdout.write(self.style.ERROR(f"COM DADOS FALTANTES: {com_erros}"))
        self.stdout.write("="*40)

        if com_erros > 0:
            self.stdout.write("\nDETALHES DOS ERROS (Primeiros 50):")
            for erro in erros_detalhes[:50]:
                self.stdout.write(erro)
            if len(erros_detalhes) > 50:
                self.stdout.write(f"... e mais {len(erros_detalhes) - 50} registros.")

        if not options['commit']:
            self.stdout.write(self.style.SUCCESS("\nMODO SIMULAÇÃO FINALIZADO."))
            self.stdout.write("Para salvar as alterações, rode com: --commit")
            self.stdout.write("Para sobrescrever etiquetas existentes, adicione: --force")
        else:
            self.stdout.write(self.style.SUCCESS("\nATUALIZAÇÃO CONCLUÍDA COM SUCESSO."))