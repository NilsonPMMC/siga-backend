"""
Comando para auditar qualidade de dados do CRM usando Gemini AI.
Identifica registros com dados pobres, telefones genéricos e sugere correções.
"""
import re
import logging
import time
from django.core.management.base import BaseCommand
from django.db.models import Q
from atendimentos.models import Municipe
from atendimentos.services.ai_service import AIService

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
    help = "Audita qualidade de dados do CRM usando Gemini AI. Identifica registros pobres e sugere correções."

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
            '--skip-ai',
            action='store_true',
            help='Apenas detecta padrões lixo com regex, sem chamar IA (mais rápido)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Força reauditoria mesmo de registros já auditados',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        limit = options.get('limit')
        skip_ai = options.get('skip_ai', False)
        force = options.get('force', False)
        
        if skip_ai:
            self.stdout.write(self.style.WARNING("Modo SKIP-AI: apenas detecção regex, sem chamar Gemini."))
        
        # Inicializar serviço de IA
        ai_service = AIService() if not skip_ai else None
        
        # Query base: registros sem auditoria ou forçar reauditoria
        queryset = Municipe.objects.all()
        if not force:
            queryset = queryset.filter(Q(auditoria_ia__isnull=True) | Q(auditoria_ia={}))
        
        if limit:
            queryset = queryset[:limit]
        
        total = queryset.count()
        self.stdout.write(f"Total de registros a processar: {total}")
        
        processados = 0
        com_problemas = 0
        sem_problemas = 0
        
        # Processar em lotes
        for offset in range(0, total, batch_size):
            batch = queryset[offset:offset + batch_size]
            self.stdout.write(f"\nProcessando lote {offset // batch_size + 1} (registros {offset + 1} a {min(offset + batch_size, total)})...")
            
            # Adicionar delay entre lotes para evitar rate limit da API (versão gratuita/padrão)
            if offset > 0 and not skip_ai:
                time.sleep(1)  # 1 segundo entre batches para respeitar limites da API
            
            for municipe in batch:
                try:
                    # Extrair dados essenciais
                    telefone_principal = ''
                    if municipe.telefones and isinstance(municipe.telefones, list) and len(municipe.telefones) > 0:
                        telefone_principal = municipe.telefones[0].get('numero', '')
                    
                    bairro = ''
                    if municipe.endereco and isinstance(municipe.endereco, dict):
                        bairro = municipe.endereco.get('bairro', '')
                    
                    dados = {
                        'nome_completo': municipe.nome_completo or '',
                        'telefone': telefone_principal,
                        'cpf': municipe.cpf or '',
                        'categoria': municipe.categoria.nome if municipe.categoria else '',
                        'orgao': municipe.orgao or '',
                        'bairro': bairro,
                    }
                    
                    # Detecção rápida de padrões "lixo" com regex (OTIMIZAÇÃO: antes de chamar IA)
                    telefone_lixo = detectar_padrao_lixo_telefone(telefone_principal)
                    nome_lixo = detectar_padrao_lixo_nome(municipe.nome_completo)
                    cpf_vazio = not municipe.cpf or municipe.cpf.strip() == ''
                    
                    # Se regex detectou padrão lixo óbvio, marcar diretamente SEM chamar IA (economiza tokens)
                    if telefone_lixo or nome_lixo or (cpf_vazio and not telefone_principal):
                        problemas = []
                        if telefone_lixo:
                            problemas.append('Telefone genérico/inválido detectado por regex')
                        if nome_lixo:
                            problemas.append('Nome fictício ou incompleto detectado por regex')
                        if cpf_vazio and not telefone_principal:
                            problemas.append('CPF ausente e sem telefone válido')
                        
                        # Calcular nota baseada na gravidade
                        if telefone_lixo and nome_lixo:
                            nota = 1  # Muito grave
                        elif telefone_lixo or nome_lixo:
                            nota = 3  # Grave
                        else:
                            nota = 5  # Moderado
                        
                        auditoria = {
                            'classificacao': 'SAD',
                            'nota_qualidade': nota,
                            'sugestao_correcao': 'Registro com dados inválidos detectados por padrões regex. Verificar manualmente e completar informações faltantes.',
                            'problemas_detectados': problemas,
                            'metodo_deteccao': 'regex'  # Indica que foi detectado por regex, não IA
                        }
                        
                        municipe.auditoria_ia = auditoria
                        municipe.save(update_fields=['auditoria_ia'])
                        com_problemas += 1
                        processados += 1
                        continue  # Pula para próximo registro, não chama IA
                    
                    # Se não detectou padrão lixo óbvio, chamar IA apenas se não estiver em modo skip-ai
                    if skip_ai:
                        # Modo skip-ai: marcar como OK se regex não detectou problemas
                        auditoria = {
                            'classificacao': 'OK',
                            'nota_qualidade': 8,
                            'sugestao_correcao': 'Nenhuma correção necessária (validação regex)',
                            'problemas_detectados': [],
                            'metodo_deteccao': 'regex'
                        }
                        municipe.auditoria_ia = auditoria
                        municipe.save(update_fields=['auditoria_ia'])
                        sem_problemas += 1
                    else:
                        # Chamar IA para análise completa (regex não detectou problemas óbvios)
                        resultado = ai_service.analisar_qualidade_registro(dados)
                        
                        # Tratar retorno de erro da IA graciosamente
                        if resultado:
                            # Verificar se é um objeto de erro padrão retornado pela IA
                            if resultado.get('status') == 'erro_ia':
                                # IA retornou erro estruturado (404, ResourceExhausted, etc)
                                self.stdout.write(self.style.WARNING(f"  IA retornou erro para {municipe.nome_completo}: {resultado.get('detalhes')}"))
                                resultado['metodo_deteccao'] = 'ia_erro'
                                municipe.auditoria_ia = resultado
                                municipe.save(update_fields=['auditoria_ia'])
                                # Considerar como sem problemas para não bloquear o processamento
                                sem_problemas += 1
                            else:
                                # Resultado válido da IA
                                resultado['metodo_deteccao'] = 'ia'  # Indica que foi detectado por IA
                                municipe.auditoria_ia = resultado
                                municipe.save(update_fields=['auditoria_ia'])
                                
                                if resultado.get('classificacao') in ('SAD', 'FALSO_POSITIVO'):
                                    com_problemas += 1
                                else:
                                    sem_problemas += 1
                        else:
                            # Se IA retornou None (erro não tratado ou exceção), usar fallback
                            self.stdout.write(self.style.WARNING(f"  IA retornou None para {municipe.nome_completo}, marcando como pendente..."))
                            auditoria = {
                                'classificacao': 'OK',
                                'nota_qualidade': 7,
                                'sugestao_correcao': 'Análise por IA indisponível. Validação regex não detectou problemas óbvios.',
                                'problemas_detectados': [],
                                'metodo_deteccao': 'regex_fallback'
                            }
                            municipe.auditoria_ia = auditoria
                            municipe.save(update_fields=['auditoria_ia'])
                            sem_problemas += 1
                        
                        processados += 1
                    
                    if processados % 10 == 0:
                        self.stdout.write(f"  Processados: {processados}/{total}")
                        
                except Exception as e:
                    logger.error(f"Erro ao processar munícipe ID {municipe.id}: {e}", exc_info=True)
                    self.stdout.write(self.style.ERROR(f"  Erro ao processar {municipe.nome_completo}: {e}"))
                    continue
        
        # Resumo final
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.SUCCESS(f"Auditoria concluída!"))
        self.stdout.write(f"Total processados: {processados}")
        self.stdout.write(self.style.WARNING(f"Registros com problemas: {com_problemas}"))
        self.stdout.write(self.style.SUCCESS(f"Registros OK: {sem_problemas}"))
        self.stdout.write("="*60)
