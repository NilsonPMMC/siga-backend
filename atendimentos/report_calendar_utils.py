"""
Montagem de grade semanal (seg–dom) para relatórios PDF em estilo calendário.
Reutilizado por relatórios de Google Agenda e de Espaços.
"""
from collections import defaultdict
from datetime import timedelta


NOMES_MESES_PT = (
    'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
)


def build_meses_do_relatorio(start_date, end_date, eventos_por_dia):
    """
    Constrói lista de meses com semanas para template de calendário.

    :param start_date: date — início do período solicitado (só eventos neste intervalo)
    :param end_date: date — fim do período solicitado
    :param eventos_por_dia: dict[date, list] — eventos indexados pelo dia de exibição
    :return: list[dict] com chaves nome_mes, mes_numero, semanas
    """
    dias_para_voltar = start_date.weekday()
    segunda_feira_inicio = start_date - timedelta(days=dias_para_voltar)

    dias_para_avancar = 6 - end_date.weekday()
    domingo_fim = end_date + timedelta(days=dias_para_avancar)

    data_atual = segunda_feira_inicio
    semanas_agrupadas_por_mes = defaultdict(list)

    while data_atual <= domingo_fim:
        mes_ano = (data_atual.year, data_atual.month)
        semana = []
        for i in range(7):
            dia_semana = data_atual + timedelta(days=i)
            if start_date <= dia_semana <= end_date:
                semana.append({
                    'data': dia_semana,
                    'eventos': eventos_por_dia.get(dia_semana, []),
                })
            else:
                semana.append({
                    'data': dia_semana,
                    'eventos': [],
                })
        semanas_agrupadas_por_mes[mes_ano].append(semana)
        data_atual += timedelta(days=7)

    meses_do_relatorio = []
    for (ano, mes), semanas in sorted(semanas_agrupadas_por_mes.items()):
        nome_mes_pt = NOMES_MESES_PT[mes - 1]
        meses_do_relatorio.append({
            'nome_mes': f'{nome_mes_pt} de {ano}',
            'mes_numero': mes,
            'semanas': semanas,
        })
    return meses_do_relatorio
