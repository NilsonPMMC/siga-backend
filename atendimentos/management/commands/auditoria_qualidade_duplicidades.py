# atendimentos/management/commands/auditoria_qualidade_duplicidades.py
"""
Comando de gestão: Auditoria de Qualidade e Duplicidades nos munícipes.
- Limpeza/normalização de CPF e telefones; marcação de telefones inválidos em auditoria_ia.
- Detecção de duplicatas em 3 níveis (CPF, contato por conta, fuzzy nome por conta).
- Atualização de grupo_duplicado (UUID) e auditoria_ia; processamento por Conta para performance.
"""
import re
import uuid
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db import transaction

from atendimentos.models import Municipe, Conta

try:
    from thefuzz import fuzz
except ImportError:
    fuzz = None  # fallback: Level 3 desativado se não tiver thefuzz

# Telefones considerados genéricos/inválidos (apenas dígitos)
TELEFONES_INVALIDOS = frozenset({
    '00000000', '000000000', '0000000000', '00000000000',
    '11111111', '111111111', '1111111111', '11111111111',
    '12345678', '123456789', '12341234', '1234567890',
    '99999999', '999999999', '9999999999', '99999999999',
})


def normalizar_cpf(val):
    """Remove pontuação do CPF e retorna só dígitos."""
    if not val or not isinstance(val, str):
        return None
    digits = re.sub(r'\D', '', val)
    return digits if len(digits) == 11 else None


def extrair_digitos_telefone(val):
    """Extrai apenas dígitos do telefone (string ou valor de dict 'numero')."""
    if val is None:
        return None
    if isinstance(val, dict):
        val = val.get('numero') or val.get('numero_whatsapp')
    if not val:
        return None
    s = re.sub(r'\D', '', str(val))
    return s if s else None


def telefone_invalido(digits):
    """Verifica se o telefone (já só dígitos) é genérico/inválido."""
    if not digits or len(digits) < 8:
        return True
    # normalizar para chave de 8-11 dígitos para comparação
    for inv in TELEFONES_INVALIDOS:
        if digits == inv or (len(digits) >= 8 and digits[:8] == inv[:8]):
            return True
    if len(digits) >= 8 and digits == digits[0] * len(digits):  # 11111111, 99999999
        return True
    return False


def obter_emails_normalizados(municipe):
    """Retorna set de emails em minúsculas."""
    out = set()
    if not municipe.emails or not isinstance(municipe.emails, list):
        return out
    for item in municipe.emails:
        if isinstance(item, dict) and item.get('email'):
            out.add(str(item['email']).strip().lower())
    return out


def obter_telefones_normalizados(municipe):
    """Retorna set de telefones (só dígitos). Invalida genéricos e retorna (digits, is_invalid)."""
    digits_set = set()
    tem_invalido = False
    if not municipe.telefones or not isinstance(municipe.telefones, list):
        return digits_set, tem_invalido
    for item in municipe.telefones:
        if not isinstance(item, dict):
            continue
        d = extrair_digitos_telefone(item)
        if d:
            digits_set.add(d)
            if telefone_invalido(d):
                tem_invalido = True
    return digits_set, tem_invalido


def bairro_normalizado(municipe):
    """Retorna bairro do endereço (string vazia se não houver)."""
    if not municipe.endereco or not isinstance(municipe.endereco, dict):
        return ''
    b = (municipe.endereco.get('bairro') or '').strip()
    return b.upper() if b else ''


def palavras_nome_guerra(municipe):
    """Retorna conjunto de palavras do nome_de_guerra (para overlap parcial)."""
    n = (municipe.nome_de_guerra or '').strip()
    if not n:
        return set()
    return set(re.sub(r'[^A-Za-zÀ-ÿ0-9\s]', ' ', n).upper().split())


class UnionFind:
    """Union-Find para unir grupos de IDs de munícipes."""

    def __init__(self, ids=None):
        self.parent = {}
        self.rank = {}
        for i in (ids or []):
            self.parent[i] = i
            self.rank[i] = 0

    def ensure(self, i):
        if i not in self.parent:
            self.parent[i] = i
            self.rank[i] = 0

    def find(self, i):
        self.ensure(i)
        if self.parent[i] != i:
            self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i, j):
        pi, pj = self.find(i), self.find(j)
        if pi == pj:
            return
        if self.rank[pi] < self.rank[pj]:
            pi, pj = pj, pi
        self.parent[pj] = pi
        if self.rank[pi] == self.rank[pj]:
            self.rank[pi] += 1

    def to_groups(self):
        groups = defaultdict(list)
        for i in self.parent:
            groups[self.find(i)].append(i)
        return {k: v for k, v in groups.items() if len(v) > 1}


class Command(BaseCommand):
    help = (
        'Auditoria de qualidade e duplicidades: normaliza dados, marca telefones inválidos '
        'e detecta duplicatas (CPF, contato por conta, fuzzy nome). Preenche grupo_duplicado.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Apenas simula; não persiste alterações.',
        )
        parser.add_argument(
            '--sem-fuzzy',
            action='store_true',
            help='Desativa detecção por similaridade de nome (Nível 3).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        sem_fuzzy = options['sem_fuzzy']

        if fuzz is None and not sem_fuzzy:
            self.stdout.write(self.style.WARNING(
                'thefuzz não instalado. Use pip install thefuzz[speedup]. Nível 3 (fuzzy) desativado.'
            ))
            sem_fuzzy = True

        self.stdout.write(self.style.SUCCESS('Iniciando Auditoria de Qualidade e Duplicidades...'))

        with transaction.atomic():
            sid = transaction.savepoint()

            try:
                # ------ 1. LIMPEZA E QUALIDADE (auditoria_ia para telefone inválido) ------
                self._etapa_higiene()
                # ------ 2. NÍVEL 1: DUPLICATAS POR CPF ------
                uf = self._etapa_cpf()
                # ------ 3. NÍVEL 2: MESMO TELEFONE OU EMAIL DENTRO DA MESMA CONTA ------
                self._etapa_contato_por_conta(uf)
                # ------ 4. NÍVEL 3: FUZZY NOME (>97%) + DADO PARCIAL (BAIRRO/NOME GUERRA) POR CONTA ------
                if not sem_fuzzy and fuzz:
                    self._etapa_fuzzy_por_conta(uf)
                # ------ 5. PERSISTIR GRUPOS E STATUS AUDITADO ------
                self._aplicar_grupos_e_auditado(uf, dry_run)

            except Exception as e:
                transaction.savepoint_rollback(sid)
                raise e

            if dry_run:
                transaction.savepoint_rollback(sid)
                self.stdout.write(self.style.WARNING('DRY-RUN: nenhuma alteração persistida.'))

        self.stdout.write(self.style.SUCCESS('Auditoria concluída.'))

    def _etapa_higiene(self):
        """Marca em auditoria_ia registros com telefone inválido; não altera grupo_duplicado aqui."""
        atualizar = []
        qs = Municipe.objects.all().only('id', 'telefones', 'auditoria_ia')
        for m in qs.iterator(chunk_size=500):
            _, tem_invalido = obter_telefones_normalizados(m)
            if tem_invalido:
                audit = dict(m.auditoria_ia) if m.auditoria_ia else {}
                audit['qualidade'] = 'baixa'
                audit['motivo'] = 'telefone_invalido'
                m.auditoria_ia = audit
                atualizar.append(m)
        if atualizar:
            Municipe.objects.bulk_update(atualizar, ['auditoria_ia'], batch_size=500)
        self.stdout.write(f'  Higiene: {len(atualizar)} registro(s) com telefone inválido marcado(s).')

    def _etapa_cpf(self):
        """Agrupa por CPF normalizado (ignorando nulos). Retorna UnionFind com todos os ids."""
        cpfs = defaultdict(list)
        qs = Municipe.objects.all().only('id', 'cpf')
        for m in qs.iterator(chunk_size=1000):
            c = normalizar_cpf(m.cpf)
            if c:
                cpfs[c].append(m.id)

        uf = UnionFind(list(Municipe.objects.values_list('id', flat=True)))
        grupos_cpf = 0
        for cpf, ids in cpfs.items():
            if len(ids) < 2:
                continue
            for i in range(1, len(ids)):
                uf.union(ids[0], ids[i])
            grupos_cpf += 1
        self.stdout.write(f'  Nível 1 (CPF): {grupos_cpf} grupo(s) de CPF duplicado.')
        return uf

    def _etapa_contato_por_conta(self, uf):
        """Por conta: agrupa quem tem mesmo telefone ou mesmo email."""
        contas = Conta.objects.all().only('id')
        for conta in contas:
            municipes = list(
                Municipe.objects.filter(contas=conta)
                .only('id', 'telefones', 'emails')
                .iterator(chunk_size=500)
            )
            if len(municipes) < 2:
                continue
            tel_to_ids = defaultdict(list)
            email_to_ids = defaultdict(list)
            for m in municipes:
                tels, _ = obter_telefones_normalizados(m)
                for t in tels:
                    if not telefone_invalido(t):
                        tel_to_ids[t].append(m.id)
                for e in obter_emails_normalizados(m):
                    email_to_ids[e].append(m.id)
            for ids in tel_to_ids.values():
                if len(ids) >= 2:
                    for i in range(1, len(ids)):
                        uf.union(ids[0], ids[i])
            for ids in email_to_ids.values():
                if len(ids) >= 2:
                    for i in range(1, len(ids)):
                        uf.union(ids[0], ids[i])
        self.stdout.write('  Nível 2 (Contato por conta): processado.')

    def _etapa_fuzzy_por_conta(self, uf):
        """Por conta: pares com similaridade de nome > 97% e dado parcial (bairro ou nome_guerra)."""
        contas = Conta.objects.all().only('id')
        LIMITE = 97  # > 97% -> usar >= 98
        pares_union = 0
        for conta in contas:
            municipes = list(
                Municipe.objects.filter(contas=conta)
                .only('id', 'nome_completo', 'nome_de_guerra', 'endereco')
            )
            if len(municipes) < 2:
                continue
            bairros = {m.id: bairro_normalizado(m) for m in municipes}
            nomes_guerra = {m.id: palavras_nome_guerra(m) for m in municipes}
            for i in range(len(municipes)):
                for j in range(i + 1, len(municipes)):
                    a, b = municipes[i], municipes[j]
                    if not a.nome_completo or not b.nome_completo:
                        continue
                    sim = fuzz.ratio(a.nome_completo.upper(), b.nome_completo.upper())
                    if sim < 98:  # > 97% => >= 98
                        continue
                    # Pelo menos um dado parcial batendo
                    bairro_ok = bairros.get(a.id) and bairros.get(b.id) and bairros[a.id] == bairros[b.id]
                    pga, pgb = nomes_guerra.get(a.id, set()), nomes_guerra.get(b.id, set())
                    nome_guerra_ok = bool(pga and pgb and (pga & pgb))
                    if bairro_ok or nome_guerra_ok:
                        uf.union(a.id, b.id)
                        pares_union += 1
        self.stdout.write(f'  Nível 3 (Fuzzy nome por conta): {pares_union} par(es) unido(s).')

    def _aplicar_grupos_e_auditado(self, uf, dry_run):
        """Aplica grupo_duplicado (UUID por grupo) e auditoria_ia status AUDITADO nos demais."""
        grupos = uf.to_groups()
        ids_em_grupo = set()
        for ids in grupos.values():
            ids_em_grupo.update(ids)

        if dry_run:
            self.stdout.write(f'  [DRY-RUN] Seriam {len(grupos)} grupo(s) de duplicatas ({len(ids_em_grupo)} munícipes).')
            return

        # Limpa grupo_duplicado de todos; em seguida preenche só os agrupados
        Municipe.objects.all().update(grupo_duplicado=None)

        to_update_grupo = []
        for _repr, ids in grupos.items():
            gid = uuid.uuid4()
            for pk in ids:
                m = Municipe(pk=pk, grupo_duplicado=gid)
                to_update_grupo.append(m)
        if to_update_grupo:
            Municipe.objects.bulk_update(to_update_grupo, ['grupo_duplicado'], batch_size=500)

        # Quem não está em grupo e não tem qualidade baixa: marcar como auditado
        todos_ids = set(Municipe.objects.values_list('id', flat=True))
        ids_sem_duplicata = todos_ids - ids_em_grupo
        auditados = []
        for m in Municipe.objects.filter(id__in=ids_sem_duplicata).only('id', 'auditoria_ia').iterator(chunk_size=500):
            # Não sobrescrever quem já tem qualidade baixa
            if m.auditoria_ia and m.auditoria_ia.get('qualidade') == 'baixa':
                continue
            audit = dict(m.auditoria_ia) if m.auditoria_ia else {}
            audit['status'] = 'AUDITADO'
            if 'qualidade' not in audit:
                audit['qualidade'] = 'ok'
            m.auditoria_ia = audit
            auditados.append(m)
        if auditados:
            Municipe.objects.bulk_update(auditados, ['auditoria_ia'], batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                f'  Aplicado: {len(grupos)} grupo(s) de duplicatas; {len(auditados)} registro(s) marcados como AUDITADO.'
            )
        )
