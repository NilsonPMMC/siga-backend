# 🔧 Correção: Alinhamento de Colunas no Relatório Google Agenda

## ❌ Problema Identificado

Quando o período selecionado não começava na segunda-feira (ex: quinta-feira), o relatório mostrava os dados desalinhados nas colunas. Isso acontecia porque:

1. O código usa `calendar.monthdatescalendar()` que sempre retorna semanas completas começando na segunda-feira
2. Quando o período começava em outro dia, os primeiros dias da semana apareciam vazios ou com dados de outro mês
3. Os eventos apareciam na coluna errada (ex: evento de quinta aparecendo na coluna de segunda)

## ✅ Solução Implementada

Ajustar automaticamente o início do período para sempre começar na **segunda-feira** da semana que contém a data inicial:

### Alterações Realizadas

1. **Cálculo do início ajustado:**
   - Calcula quantos dias precisa voltar para chegar na segunda-feira
   - Ajusta `start_date` para começar na segunda-feira da semana
   - Mantém `start_date_original` para filtrar eventos corretamente

2. **Cálculo do fim ajustado:**
   - Ajusta o fim para terminar no **domingo** da semana que contém a data final
   - Garante semanas completas no calendário

3. **Filtro de eventos:**
   - Continua usando `start_date_original` para filtrar eventos
   - Apenas o calendário visual é ajustado para começar na segunda-feira

## 📋 Código Alterado

### Antes:
```python
start_date = parse_datetime(start_date_str)
end_date = parse_datetime(end_date_str).replace(hour=23, minute=59, second=59)

# Usava start_date diretamente, causando desalinhamento
data_corrente = start_date.date()
```

### Depois:
```python
start_date = parse_datetime(start_date_str)
end_date = parse_datetime(end_date_str).replace(hour=23, minute=59, second=59)

# Ajusta início para sempre começar na segunda-feira
start_date_original = start_date.date()
dias_para_voltar = start_date_original.weekday()  # 0=Seg, 1=Ter, ..., 6=Dom
start_date_ajustado = start_date_original - timedelta(days=dias_para_voltar)
start_date = datetime.combine(start_date_ajustado, start_date.time())

# Ajusta fim para terminar no domingo
dias_para_avancar = 6 - data_final_loop.weekday()
data_final_loop_ajustada = data_final_loop + timedelta(days=dias_para_avancar)

# Usa data ajustada para construir calendário, mas filtra eventos pela original
data_corrente = start_date.date()
```

## 🎯 Resultado

Agora, independente do dia da semana selecionado:
- ✅ O calendário sempre começa na **segunda-feira**
- ✅ Os eventos aparecem na **coluna correta**
- ✅ As semanas ficam **completas e alinhadas**
- ✅ Os eventos são filtrados corretamente pela data original

## 📝 Exemplo

**Antes:**
- Período selecionado: Quinta-feira (05/02) a Sexta-feira (06/02)
- Resultado: Calendário começava na segunda (02/02), mas eventos só apareciam a partir de quinta
- Problema: Colunas vazias no início, eventos desalinhados

**Depois:**
- Período selecionado: Quinta-feira (05/02) a Sexta-feira (06/02)
- Resultado: Calendário começa na segunda (02/02), eventos aparecem corretamente nas colunas de quinta e sexta
- Solução: Semanas completas, eventos alinhados corretamente

## ✅ Benefícios

1. **Alinhamento correto**: Eventos sempre aparecem na coluna do dia correto
2. **Visual consistente**: Semanas sempre completas (segunda a domingo)
3. **Filtro preciso**: Eventos são filtrados pela data original selecionada
4. **Sem mudanças no frontend**: Solução totalmente no backend

## 🔍 Validação

Para testar:
1. Selecione um período que comece em qualquer dia da semana (ex: quinta-feira)
2. Gere o relatório PDF
3. Verifique se os eventos aparecem na coluna correta
4. Verifique se a semana começa na segunda-feira

---

**Data da Correção**: 10/02/2026  
**Arquivo Alterado**: `atendimentos/views.py` (classe `GerarPdfGoogleAgendaView`)
