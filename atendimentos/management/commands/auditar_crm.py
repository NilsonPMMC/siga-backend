"""
Comando para auditar qualidade de dados do CRM usando Gemini AI.
Identifica registros com dados pobres, telefones genéricos e sugere correções.
"""
import re
import logging
from django.core.management.base import BaseCommand
from django.db.models import Q
from atendimentos.models import Municipe
from atendimentos.services.ai_service import AIService

logger = logging.getLogger(__name__)


def detectar_padrao_lixo_telefone(telefone_str):
    """
    Detecta padrões óbvios de telefones inválidos usando regex.
    Retorna True se o telefone for claramente inválido/genérico.
    
    Padrões detectados:
    - Sequências repetitivas: 00000000, 99999999, 11111111
    - Sequências óbvias: 12345678, 123456789
    - Padrão genérico: (99) 99999-9999
    """
    if not telefone_str:
        return False
    
    # Remove caracteres não numéricos
    numeros = re.sub(r'\D', '', telefone_str)
    
    if len(numeros) < 8:
        return False
    
    # Padrão genérico (99) 99999-9999
    if re.match(r'^9{2}9{5}9{4}$', numeros) or re.match(r'^9{10,11}$', numeros):
        return True
    
    # Sequências repetitivas (todos os dígitos iguais)
    if len(set(numeros)) == 1:
        return True
    
    # Sequências óbvias (12345678, 123456789, etc)
    if re.match(r'^12345678', numeros) or re.match(r'^123456789', numeros):
        return True
    
    # Sequências decrescentes (98765432)
    if re.match(r'^98765432', numeros):
        return True
    
    return False


def detectar_padrao_lixo_nome(nome):
    """
    Detecta nomes claramente fictícios ou incompletos.
    """
    if not nome:
        return True
    
    nome_upper = nome.upper().strip()
    
    # Nomes genéricos/fictícios comuns
    nomes_lixo = [
        'TESTE', 'FULANO', 'CICLANO', 'BELTRANO', 'NOME COMPLETO',
        'NOME', 'SOBRENOME', 'EXEMPLO', 'DEMONSTRACAO', 'DEMO',
        'XXX', 'YYY', 'ZZZ', 'ABC', '123', 'SEM NOME'
    ]
    
    if nome_upper in nomes_lixo:
        return True
    
    # Nome muito curto (menos de 3 caracteres)
    if len(nome_upper.replace(' ', '')) < 3:
        return True
    
    # Apenas números
    if nome_upper.replace(' ', '').isdigit():
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
                    
                    # Detecção rápida de padrões "lixo" com regex
                    telefone_lixo = detectar_padrao_lixo_telefone(telefone_principal)
                    nome_lixo = detectar_padrao_lixo_nome(municipe.nome_completo)
                    cpf_vazio = not municipe.cpf or municipe.cpf.strip() == ''
                    
                    # Se detectou padrão lixo óbvio e está em modo skip-ai, marcar diretamente
                    if skip_ai:
                        if telefone_lixo or nome_lixo or (cpf_vazio and not telefone_principal):
                            auditoria = {
                                'classificacao': 'SAD',
                                'nota_qualidade': 2 if telefone_lixo and nome_lixo else 4,
                                'sugestao_correcao': 'Registro com dados inválidos detectados por padrões regex. Verificar manualmente.',
                                'problemas_detectados': []
                            }
                            if telefone_lixo:
                                auditoria['problemas_detectados'].append('Telefone genérico/inválido')
                            if nome_lixo:
                                auditoria['problemas_detectados'].append('Nome fictício ou incompleto')
                            if cpf_vazio:
                                auditoria['problemas_detectados'].append('CPF ausente')
                            
                            municipe.auditoria_ia = auditoria
                            municipe.save(update_fields=['auditoria_ia'])
                            com_problemas += 1
                        else:
                            auditoria = {
                                'classificacao': 'OK',
                                'nota_qualidade': 8,
                                'sugestao_correcao': 'Nenhuma correção necessária (validação regex)',
                                'problemas_detectados': []
                            }
                            municipe.auditoria_ia = auditoria
                            municipe.save(update_fields=['auditoria_ia'])
                            sem_problemas += 1
                    else:
                        # Chamar IA para análise completa
                        resultado = ai_service.analisar_qualidade_registro(dados)
                        
                        if resultado:
                            # Se regex detectou padrão lixo mas IA não classificou como SAD, adicionar flag
                            if (telefone_lixo or nome_lixo) and resultado.get('classificacao') != 'SAD':
                                resultado['problemas_detectados'] = resultado.get('problemas_detectados', [])
                                if telefone_lixo:
                                    resultado['problemas_detectados'].append('Telefone genérico detectado por regex')
                                if nome_lixo:
                                    resultado['problemas_detectados'].append('Nome suspeito detectado por regex')
                                # Ajustar nota se necessário
                                if resultado.get('nota_qualidade', 10) > 5:
                                    resultado['nota_qualidade'] = max(3, resultado['nota_qualidade'] - 2)
                            
                            municipe.auditoria_ia = resultado
                            municipe.save(update_fields=['auditoria_ia'])
                            
                            if resultado.get('classificacao') in ('SAD', 'FALSO_POSITIVO'):
                                com_problemas += 1
                            else:
                                sem_problemas += 1
                        else:
                            # Se IA falhou, usar detecção regex como fallback
                            self.stdout.write(self.style.WARNING(f"  IA falhou para {municipe.nome_completo}, usando detecção regex..."))
                            if telefone_lixo or nome_lixo or cpf_vazio:
                                auditoria = {
                                    'classificacao': 'SAD',
                                    'nota_qualidade': 3,
                                    'sugestao_correcao': 'Análise por IA indisponível. Padrões suspeitos detectados por regex.',
                                    'problemas_detectados': []
                                }
                                if telefone_lixo:
                                    auditoria['problemas_detectados'].append('Telefone genérico')
                                if nome_lixo:
                                    auditoria['problemas_detectados'].append('Nome suspeito')
                                if cpf_vazio:
                                    auditoria['problemas_detectados'].append('CPF ausente')
                                
                                municipe.auditoria_ia = auditoria
                                municipe.save(update_fields=['auditoria_ia'])
                                com_problemas += 1
                            else:
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
