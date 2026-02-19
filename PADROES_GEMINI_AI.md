# Google Gemini AI - Padrões e Boas Práticas

## Modelo Padrão

**SEMPRE use o modelo `gemini-2.0-flash`** (sem prefixo `models/`):

```python
# ✅ CORRETO
self.model_name = 'gemini-2.0-flash'

# ❌ ERRADO
self.model_name = 'gemini-1.5-flash'
self.model_name = 'models/gemini-2.0-flash'
```

## Tratamento de Erros de Cota

### Erros 429 (Quota Exceeded) e 503 (Service Unavailable)

**SEMPRE** capture e trate esses erros graciosamente:

```python
# ✅ CORRETO
try:
    response = self.client.models.generate_content(
        model=self.model_name,
        contents=prompt
    )
except Exception as api_error:
    error_msg = str(api_error)
    error_type = type(api_error).__name__
    
    if '429' in error_msg or 'ResourceExhausted' in error_type or 'QuotaExceeded' in error_type:
        logger.warning("Quota da API Gemini excedida (429). Retornando retry_later.")
        return {"status": "retry_later", "msg": "Cota do Google atingida. Aguarde alguns minutos antes de tentar novamente."}
    elif '503' in error_msg or 'ServiceUnavailable' in error_type or 'ServerOverloaded' in error_type:
        logger.warning("Servidor Gemini sobrecarregado (503). Retornando retry_later.")
        return {"status": "retry_later", "msg": "Servidor do Google sobrecarregado. Aguarde alguns minutos antes de tentar novamente."}
```

### Retorno Padronizado

Quando cota é atingida, **SEMPRE** retorne:

```python
{"status": "retry_later", "msg": "Mensagem amigável explicando o problema"}
```

## Rate Limiting

### Delay Antes de Chamadas à API

**SEMPRE** adicione delay antes de chamar a API:

```python
# ✅ CORRETO - No AIService
time.sleep(2)  # Delay básico antes de cada chamada

# ✅ CORRETO - No comando de auditoria
time.sleep(10)  # Delay maior para comandos em lote
```

### Delay Entre Registros

Em comandos que processam múltiplos registros:

```python
# ✅ CORRETO
for registro in registros:
    time.sleep(10)  # Delay antes de chamar IA
    resultado = ai_service.processar(registro)
    # Processar resultado...
```

## Proteção Contra Travamento

### Try/Except em Comandos de Management

**SEMPRE** envolva chamadas à IA em try/except em comandos Django:

```python
# ✅ CORRETO
try:
    resultado = ai_service.analisar_qualidade_registro(dados)
except Exception as e:
    error_msg = str(e)
    self.stdout.write(self.style.ERROR(f"  Erro ao processar {registro.nome}: {error_msg}"))
    logger.error(f"Erro ao chamar IA: {error_msg}", exc_info=True)
    
    # Marcar como pendente e continuar
    registro.marcar_como_pendente()
    continue  # Não travar o processamento
```

### Mensagens Amigáveis

**SEMPRE** exiba mensagens claras quando erros ocorrem:

```python
# ✅ CORRETO
if resultado.get('status') == 'retry_later':
    msg_amigavel = resultado.get('msg', 'Cota do Google atingida')
    self.stdout.write(self.style.WARNING(f"  ⚠️  {msg_amigavel} - Registro será processado na próxima rodada."))
```

## Resiliência

### Continuidade do Processamento

O sistema **NUNCA** deve travar quando:
- Cota é atingida (429)
- Servidor está sobrecarregado (503)
- Erros inesperados ocorrem

**SEMPRE**:
1. Capture o erro
2. Registre no log
3. Marque o registro como pendente
4. Continue processando os próximos registros

## Checklist

Ao trabalhar com Google Gemini AI:

- [ ] Modelo está configurado como `gemini-2.0-flash`?
- [ ] Delay está sendo aplicado antes de cada chamada?
- [ ] Erros 429/503 estão sendo tratados?
- [ ] Retorno `retry_later` está sendo usado quando apropriado?
- [ ] Try/except está protegendo chamadas em comandos?
- [ ] Mensagens de erro são amigáveis e informativas?
- [ ] Sistema continua processando mesmo com erros?

## Arquivos Relacionados

- `atendimentos/services/ai_service.py` - Serviço principal de IA
- `atendimentos/management/commands/auditar_crm.py` - Comando de auditoria
