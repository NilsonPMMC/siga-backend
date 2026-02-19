"""
Comando para auditar qualidade de dados do CRM usando IA Local (Sentence-Transformers).
Identifica registros com dados pobres, telefones genéricos e sugere correções.
Também detecta duplicidades usando similaridade semântica.
"""
import re
import logging
from django.core.management.base import BaseCommand
from django.db.models import Q
from atendimentos.models import Municipe
from atendimentos.services.ai_service import LocalAIService

logger = logging.getLogger(__name__)


def detectar_padrao_lixo_telefone(telefone_str):
    """
    Detecta padrões óbvios de telefones inválidos usando regex.
    Retorna True se o telefone for claramente inválido/genérico.
    Otimizado para detectar mais padrões antes de chamar IA.
    
    Padrões detectados:
    - Sequências repetitivas: 00000000, 99999999, 11111111
    - Sequências óbvias: 12345678, 123456789, 123456
    - Padrão genérico: (99) 99999-9999
    - Campos com apenas 1 dígito repetido múltiplas vezes
    - Sequências decrescentes: 98765432
    """
    if not telefone_str:
        return False
    
    # Remove caracteres não numéricos
    numeros = re.sub(r'\D', '', telefone_str)
    
    # Telefone muito curto (menos de 8 dígitos) ou muito longo (mais de 11)
    if len(numeros) < 8 or len(numeros) > 11:
        return False
    
    # Padrão genérico (99) 99999-9999 ou variações
    if re.match(r'^9{8,11}$', numeros):  # Apenas 9s
        return True
    
    # Sequências repetitivas (todos os dígitos iguais): 00000000, 11111111, etc
    if len(set(numeros)) == 1:
        return True
    
    # Sequências óbvias crescentes (12345678, 123456789, 123456, etc)
    if re.match(r'^12345', numeros):  # Começa com 12345
        return True
    
    # Sequências decrescentes (98765432, 987654321, etc)
    if re.match(r'^98765', numeros):  # Começa com 98765
        return True
    
    # Padrão alternado simples (12121212, 01010101)
    if len(numeros) >= 8:
        # Verifica se é apenas dois dígitos alternados
        if len(set(numeros)) == 2:
            padrao = numeros[:2]
            if numeros == (padrao * (len(numeros) // 2))[:len(numeros)]:
                return True
    
    # Telefone com apenas zeros ou apenas uns (muito suspeito)
    if numeros == '0' * len(numeros) or numeros == '1' * len(numeros):
        return True
    
    return False


def detectar_padrao_lixo_nome(nome):
    """
    Detecta nomes claramente fictícios ou incompletos.
    Otimizado para detectar mais padrões antes de chamar IA.
    """
    if not nome:
        return True
    
    nome_upper = nome.upper().strip()
    nome_sem_espacos = nome_upper.replace(' ', '')
    
    # Nomes genéricos/fictícios comuns
    nomes_lixo = [
        'TESTE', 'FULANO', 'CICLANO', 'BELTRANO', 'NOME COMPLETO',
        'NOME', 'SOBRENOME', 'EXEMPLO', 'DEMONSTRACAO', 'DEMO',
        'XXX', 'YYY', 'ZZZ', 'ABC', '123', 'SEM NOME', 'NAO INFORMADO',
        'N/A', 'NA', 'NÃO INFORMADO', 'SEM NOME', 'NOME TESTE',
        'FULANO DE TAL', 'JOAO SILVA', 'MARIA SANTOS'  # Nomes genéricos muito comuns
    ]
    
    if nome_upper in nomes_lixo:
        return True
    
    # Nome muito curto (menos de 3 caracteres)
    if len(nome_sem_espacos) < 3:
        return True
    
    # Apenas números
    if nome_sem_espacos.isdigit():
        return True
    
    # Apenas uma letra repetida (AAA, BBB, etc)
    if len(set(nome_sem_espacos)) == 1 and len(nome_sem_espacos) > 2:
        return True
    
    # Padrão sequencial de letras (ABC, ABCD, etc)
    if len(nome_sem_espacos) >= 3 and nome_sem_espacos.isalpha():
        # Verifica se é sequência alfabética (ABC, ABCD, etc)
        sequencia_asc = True
        sequencia_desc = True
        for i in range(len(nome_sem_espacos) - 1):
            if ord(nome_sem_espacos[i+1]) != ord(nome_sem_espacos[i]) + 1:
                sequencia_asc = False
            if ord(nome_sem_espacos[i+1]) != ord(nome_sem_espacos[i]) - 1:
                sequencia_desc = False
        if sequencia_asc or sequencia_desc:
            return True
    
    # Nome com apenas caracteres especiais ou números misturados de forma suspeita
    if re.match(r'^[^A-Z\s]+$', nome_upper):  # Apenas não-letras (exceto espaços)
        return True
    
    return False


class Command(BaseCommand):
    help = "Audita qualidade de dados do CRM usando IA Local (Sentence-Transformers). Identifica registros pobres, sugere correções e detecta duplicidades."

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Número de registros processados por lote (default: 50)',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limite de registros a processar (útil para testes)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Força reauditoria mesmo de registros já auditados',
        )
        parser.add_argument(
            '--similarity-threshold',
            type=float,
            default=0.85,
            help='Threshold de similaridade para detectar duplicatas (default: 0.85)',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        limit = options.get('limit')
        force = options.get('force', False)
        similarity_threshold = options.get('similarity_threshold', 0.85)
        
        self.stdout.write(self.style.SUCCESS("Inicializando IA Local (Sentence-Transformers)..."))
        
        # Inicializar serviço de IA Local (Singleton - carrega modelo apenas uma vez)
        local_ai = LocalAIService()
        
        if local_ai._model is None:
            self.stdout.write(self.style.ERROR("ERRO: Modelo Sentence-Transformer não pôde ser carregado!"))
            self.stdout.write(self.style.ERROR("Instale as dependências: pip install sentence-transformers scikit-learn rapidfuzz"))
            return
        
        self.stdout.write(self.style.SUCCESS("IA Local inicializada com sucesso!"))
        
        # Query base: registros sem auditoria ou forçar reauditoria
        queryset = Municipe.objects.all()
        if not force:
            queryset = queryset.filter(Q(auditoria_ia__isnull=True) | Q(auditoria_ia={}))
        
        if limit:
            queryset = queryset[:limit]
        
        total = queryset.count()
        self.stdout.write(f"\nTotal de registros a processar: {total}")
        
        processados = 0
        com_problemas = 0
        sem_problemas = 0
        duplicatas_encontradas = 0
        
        # Processar em lotes de 50
        for offset in range(0, total, batch_size):
            batch = queryset[offset:offset + batch_size]
            self.stdout.write(f"\n{'='*60}")
            self.stdout.write(f"Processando lote {offset // batch_size + 1} (registros {offset + 1} a {min(offset + batch_size, total)})...")
            self.stdout.write(f"{'='*60}")
            
            for municipe in batch:
                try:
                    # Extrair dados essenciais
                    telefone_principal = ''
                    if municipe.telefones and isinstance(municipe.telefones, list) and len(municipe.telefones) > 0:
                        telefone_principal = municipe.telefones[0].get('numero', '')
                    
                    nome_completo = municipe.nome_completo or ''
                    
                    # 1. Análise de qualidade usando IA Local
                    resultado_qualidade = local_ai.classificar_lixo_semantico(nome_completo, telefone_principal)
                    
                    # 2. Buscar duplicidades usando similaridade semântica
                    duplicatas_potenciais = []
                    try:
                        # Buscar outros municipes com nomes similares (excluindo o atual)
                        outros_municipes = Municipe.objects.exclude(id=municipe.id).exclude(
                            nome_completo__isnull=True
                        ).exclude(nome_completo='').values_list('nome_completo', flat=True)[:100]  # Limitar a 100 para performance
                        
                        if outros_municipes:
                            lista_nomes = list(outros_municipes)
                            # Usar threshold configurado diretamente no método
                            duplicatas_potenciais = local_ai.calcular_similaridade_duplicados(
                                nome_completo, 
                                lista_nomes,
                                threshold=similarity_threshold
                            )
                    except Exception as e:
                        logger.warning(f"Erro ao buscar duplicatas para {nome_completo}: {e}")
                    
                    # 3. Montar resultado da auditoria
                    problemas = resultado_qualidade.get('problemas_detectados', [])
                    
                    # Adicionar informações de duplicatas se encontradas
                    if duplicatas_potenciais:
                        duplicatas_encontradas += len(duplicatas_potenciais)
                        problemas.append(f"Possíveis duplicatas encontradas: {len(duplicatas_potenciais)} registro(s) similar(es)")
                        
                        # Ajustar nota se houver duplicatas
                        nota_qualidade = resultado_qualidade.get('nota_qualidade', 100)
                        nota_qualidade = max(0, nota_qualidade - (len(duplicatas_potenciais) * 5))
                        resultado_qualidade['nota_qualidade'] = nota_qualidade
                    
                    # Determinar classificação
                    if resultado_qualidade.get('eh_lixo', False):
                        classificacao = 'SAD'
                        com_problemas += 1
                    elif duplicatas_potenciais:
                        classificacao = 'DUPLICATA_POTENCIAL'
                        com_problemas += 1
                    else:
                        classificacao = 'OK'
                        sem_problemas += 1
                    
                    # Montar auditoria completa
                    auditoria = {
                        'classificacao': classificacao,
                        'nota_qualidade': resultado_qualidade.get('nota_qualidade', 100),
                        'sugestao_correcao': resultado_qualidade.get('sugestao_correcao', 'Nenhuma correção necessária'),
                        'problemas_detectados': problemas,
                        'metodo_deteccao': 'ia_local_sentence_transformers',
                        'duplicatas_potenciais': [
                            {
                                'nome': dup['nome'],
                                'similaridade': round(dup['similaridade'], 3)
                            }
                            for dup in duplicatas_potenciais[:5]  # Limitar a 5 duplicatas
                        ] if duplicatas_potenciais else []
                    }
                    
                    # Salvar auditoria
                    municipe.auditoria_ia = auditoria
                    municipe.save(update_fields=['auditoria_ia'])
                    
                    processados += 1
                    
                    # Feedback no console
                    if duplicatas_potenciais:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  [{processados}/{total}] {nome_completo[:50]} - "
                                f"Nota: {resultado_qualidade.get('nota_qualidade', 100)}/100 - "
                                f"⚠️ {len(duplicatas_potenciais)} duplicata(s) potencial(is)"
                            )
                        )
                    elif resultado_qualidade.get('eh_lixo', False):
                        self.stdout.write(
                            self.style.ERROR(
                                f"  [{processados}/{total}] {nome_completo[:50]} - "
                                f"Nota: {resultado_qualidade.get('nota_qualidade', 100)}/100 - "
                                f"❌ Dados de baixa qualidade"
                            )
                        )
                    else:
                        if processados % 10 == 0:  # Mostrar apenas a cada 10 registros OK
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"  [{processados}/{total}] Processando... ({sem_problemas} OK, {com_problemas} com problemas)"
                                )
                            )
                    
                except Exception as e:
                    logger.error(f"Erro ao processar munícipe ID {municipe.id}: {e}", exc_info=True)
                    self.stdout.write(self.style.ERROR(f"  Erro ao processar {municipe.nome_completo}: {e}"))
                    continue
        
        # Resumo final
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.SUCCESS("Auditoria concluída!"))
        self.stdout.write(f"Total processados: {processados}")
        self.stdout.write(self.style.WARNING(f"Registros com problemas: {com_problemas}"))
        self.stdout.write(self.style.SUCCESS(f"Registros OK: {sem_problemas}"))
        self.stdout.write(self.style.WARNING(f"Duplicatas potenciais encontradas: {duplicatas_encontradas}"))
        self.stdout.write("="*60)
