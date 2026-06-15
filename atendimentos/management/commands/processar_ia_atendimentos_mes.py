"""
Processa em lote a análise IA (resumo, embedding, assunto) dos atendimentos de um mês.

Uso típico (cron noturno):
  python manage.py processar_ia_atendimentos_mes --limite 150

Backfill do mês corrente (uma vez):
  python manage.py processar_ia_atendimentos_mes --forcar --aplicar-assunto --limite 500
"""
import calendar
import time
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from atendimentos.models import Atendimento
from atendimentos.services.analise_ia_atendimento import processar_analise_ia_atendimento


class Command(BaseCommand):
    help = (
        "Aplica análise IA (resumo, vetor, assunto) nos atendimentos do mês de referência. "
        "Por padrão processa apenas os que ainda precisam de alguma etapa."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--referencia',
            type=str,
            default=None,
            help='Mês no formato YYYY-MM. Padrão: mês atual (timezone local).',
        )
        parser.add_argument(
            '--conta-id',
            type=int,
            default=None,
            help='Filtra por conta/gabinete.',
        )
        parser.add_argument(
            '--limite',
            type=int,
            default=100,
            help='Máximo de atendimentos por execução (default: 100).',
        )
        parser.add_argument(
            '--intervalo',
            type=float,
            default=2.0,
            help='Segundos de pausa entre cada atendimento (default: 2).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Lista o que seria processado, sem chamar a IA.',
        )
        parser.add_argument(
            '--forcar',
            action='store_true',
            help='Reprocessa todos do período, mesmo já processados.',
        )
        parser.add_argument(
            '--aplicar-assunto',
            action='store_true',
            help='Aplica o assunto sugerido (respeita limiar de confiança se auto).',
        )
        parser.add_argument(
            '--sem-aplicar-assunto',
            action='store_true',
            help='Apenas sugere assunto (status PENDENTE), não preenche o campo assunto.',
        )
        parser.add_argument(
            '--sem-resumo',
            action='store_true',
            help='Pula resumo e embedding; só classifica assunto.',
        )
        parser.add_argument(
            '--sem-assunto',
            action='store_true',
            help='Pula classificação de assunto; só resumo e embedding.',
        )

    def _parse_referencia(self, referencia):
        if not referencia:
            agora = timezone.localtime()
            return agora.year, agora.month
        try:
            partes = referencia.strip().split('-')
            if len(partes) != 2:
                raise ValueError
            ano, mes = int(partes[0]), int(partes[1])
            if mes < 1 or mes > 12:
                raise ValueError
            return ano, mes
        except ValueError as exc:
            raise CommandError(
                f'Referência inválida: {referencia}. Use YYYY-MM (ex.: 2026-05).'
            ) from exc

    def _intervalo_mes(self, ano, mes):
        ultimo_dia = calendar.monthrange(ano, mes)[1]
        inicio = timezone.make_aware(datetime(ano, mes, 1, 0, 0, 0))
        fim = timezone.make_aware(datetime(ano, mes, ultimo_dia, 23, 59, 59, 999999))
        return inicio, fim

    def _queryset(self, inicio, fim, conta_id, forcar):
        qs = (
            Atendimento.objects.filter(data_criacao__range=(inicio, fim))
            .select_related('municipe', 'conta', 'assunto', 'assunto_ia_sugerido')
            .order_by('data_criacao', 'id')
        )
        if conta_id:
            qs = qs.filter(conta_id=conta_id)
        if not forcar:
            qs = qs.filter(
                Q(auditoria_ia_status__in=('PENDENTE', 'ERRO'))
                | Q(resumo_ia_local__isnull=True)
                | Q(resumo_ia_local='')
                | Q(assunto_ia_status__isnull=True)
                | Q(assunto_ia_status__in=('PENDENTE', 'ERRO'))
                | Q(assunto__isnull=True)
            )
        return qs

    def handle(self, *args, **options):
        ano, mes = self._parse_referencia(options.get('referencia'))
        inicio, fim = self._intervalo_mes(ano, mes)
        conta_id = options.get('conta_id')
        limite = options['limite']
        dry_run = options['dry_run']
        forcar = options['forcar']
        intervalo = max(0.0, float(options['intervalo']))

        sem_aplicar = options.get('sem_aplicar_assunto')
        aplicar_flag = options.get('aplicar_assunto')
        if sem_aplicar:
            aplicar_assunto = False
        elif aplicar_flag:
            aplicar_assunto = True
        else:
            aplicar_assunto = getattr(settings, 'ATENDIMENTO_ASSUNTO_IA_AUTO_APLICAR', False)

        processar_resumo = not options.get('sem_resumo')
        processar_assunto = not options.get('sem_assunto')

        qs = self._queryset(inicio, fim, conta_id, forcar)
        total_periodo = qs.count()
        atendimentos = list(qs[:limite])
        total_lote = len(atendimentos)

        self.stdout.write(self.style.NOTICE('=' * 72))
        self.stdout.write(self.style.NOTICE('Rotina: processar_ia_atendimentos_mes'))
        self.stdout.write(
            self.style.NOTICE(
                f'Período: {inicio.strftime("%d/%m/%Y")} — {fim.strftime("%d/%m/%Y")} '
                f'({ano}-{mes:02d})'
            )
        )
        if conta_id:
            self.stdout.write(self.style.NOTICE(f'Conta ID: {conta_id}'))
        self.stdout.write(
            self.style.NOTICE(
                f'Pendentes no período: {total_periodo} | Neste lote: {total_lote} | '
                f'Forçar: {forcar} | Aplicar assunto: {aplicar_assunto}'
            )
        )
        self.stdout.write(
            self.style.NOTICE(
                f'Etapas: resumo/vetor={"sim" if processar_resumo else "não"} | '
                f'assunto={"sim" if processar_assunto else "não"}'
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING('Modo DRY-RUN (sem chamadas à IA).'))
        self.stdout.write(self.style.NOTICE('=' * 72))

        if total_lote == 0:
            self.stdout.write(
                self.style.SUCCESS('Nenhum atendimento a processar para os filtros informados.')
            )
            return

        if dry_run:
            for a in atendimentos:
                self.stdout.write(
                    f'  - {a.protocolo} id={a.id} '
                    f'auditoria={a.auditoria_ia_status} assunto_ia={a.assunto_ia_status} '
                    f'assunto_id={a.assunto_id}'
                )
            self.stdout.write(
                self.style.SUCCESS(f'[DRY-RUN] {total_lote} atendimento(s) listado(s).')
            )
            return

        ok = 0
        parcial = 0
        erros = 0
        stats = {'resumo_ok': 0, 'vetor_ok': 0, 'assunto_ok': 0, 'assunto_aplicado': 0}

        for i, atendimento in enumerate(atendimentos, start=1):
            self.stdout.write(
                f'[{i}/{total_lote}] {atendimento.protocolo}...',
                ending=' ',
            )
            resultado = processar_analise_ia_atendimento(
                atendimento,
                aplicar_assunto=aplicar_assunto,
                processar_resumo=processar_resumo,
                processar_vetor=processar_resumo,
                processar_assunto=processar_assunto,
                forcar=forcar,
            )
            if resultado['resumo'] == 'ok':
                stats['resumo_ok'] += 1
            if resultado['vetor'] == 'ok':
                stats['vetor_ok'] += 1
            if resultado['assunto'] in ('sugerido', 'aplicado', 'ja_processado'):
                stats['assunto_ok'] += 1
            if resultado['assunto'] == 'aplicado':
                stats['assunto_aplicado'] += 1

            if resultado['ok'] and not resultado['detalhes']:
                ok += 1
                self.stdout.write(self.style.SUCCESS('OK'))
            elif resultado['ok'] or resultado['detalhes']:
                parcial += 1
                self.stdout.write(
                    self.style.WARNING(
                        f'PARCIAL ({", ".join(resultado["detalhes"]) or resultado["assunto"]})'
                    )
                )
            else:
                erros += 1
                self.stdout.write(
                    self.style.ERROR(
                        f'ERRO ({", ".join(resultado["detalhes"])})'
                    )
                )

            if i < total_lote and intervalo > 0:
                time.sleep(intervalo)

        self.stdout.write(self.style.NOTICE('-' * 72))
        self.stdout.write(
            self.style.SUCCESS(
                f'Resumo lote: ok={ok} parcial={parcial} erros={erros} | '
                f'resumos={stats["resumo_ok"]} vetores={stats["vetor_ok"]} '
                f'assuntos={stats["assunto_ok"]} aplicados={stats["assunto_aplicado"]} | '
                f'restantes_no_periodo={max(0, total_periodo - total_lote)}'
            )
        )
        if total_periodo > total_lote:
            self.stdout.write(
                self.style.WARNING(
                    f'Ainda há ~{total_periodo - total_lote} atendimento(s) no período. '
                    'Execute novamente ou aumente --limite.'
                )
            )
