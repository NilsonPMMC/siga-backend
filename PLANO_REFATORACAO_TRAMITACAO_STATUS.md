# 📋 Plano de Refatoração: Tramitação Obrigatória e Integração Sinapse

## 🎯 Objetivo

Alinhar o fluxo de status com tramitação/andamento, tornando a tramitação **obrigatória** para alterar o status do atendimento. Para status "Encaminhado", integrar com API Sinapse para indicar a estrutura organizacional (secretarias e hierarquias).

## 📊 Situação Atual

### Problemas Identificados:
1. ❌ Status pode ser alterado diretamente sem tramitação
2. ❌ Tramitação é opcional e não está vinculada à mudança de status
3. ❌ Status "Encaminhado" não indica para onde foi encaminhado
4. ❌ Falta de dados qualitativos sobre o andamento

### Estrutura Atual:
- **Atendimento**: Tem campo `status` que pode ser alterado livremente
- **Tramitacao**: Modelo separado com `despacho` e `usuario`
- **Sem vínculo**: Tramitação não altera status automaticamente

## ✅ Solução Proposta

### Fase 1: Modelo de Dados

#### 1.1. Atualizar Modelo `Tramitacao`

**Campos a adicionar:**
```python
class Tramitacao(UppercaseFieldsMixin, models.Model):
    atendimento = models.ForeignKey(Atendimento, ...)
    despacho = models.TextField(...)
    usuario = models.ForeignKey(User, ...)
    data_tramitacao = models.DateTimeField(...)
    
    # NOVOS CAMPOS:
    status_anterior = models.CharField(max_length=20, choices=Atendimento.STATUS_CHOICES, null=True, blank=True)
    status_novo = models.CharField(max_length=20, choices=Atendimento.STATUS_CHOICES, null=True, blank=True)
    
    # Para status ENCAMINHADO:
    encaminhado_para_sinapse_id = models.IntegerField(null=True, blank=True, verbose_name="ID Sinapse (Secretaria/Órgão)")
    encaminhado_para_nome = models.CharField(max_length=255, null=True, blank=True, verbose_name="Nome do Destino (Sinapse)")
    encaminhado_para_tipo = models.CharField(max_length=50, null=True, blank=True, verbose_name="Tipo (Secretaria/Setor/etc)")
    
    # Campos de auditoria
    alterou_status = models.BooleanField(default=False, verbose_name="Esta tramitação alterou o status?")
```

#### 1.2. Criar Modelo de Cache Sinapse (Opcional mas Recomendado)

```python
class SinapseSecretaria(models.Model):
    """
    Cache local da estrutura organizacional da API Sinapse.
    Atualizado periodicamente via comando de management.
    """
    sinapse_id = models.IntegerField(unique=True, verbose_name="ID Sinapse")
    nome = models.CharField(max_length=255, verbose_name="Nome da Secretaria/Órgão")
    sigla = models.CharField(max_length=50, blank=True, null=True)
    tipo = models.CharField(max_length=50, verbose_name="Tipo (Secretaria, Setor, etc)")
    hierarquia = models.JSONField(null=True, blank=True, help_text="Estrutura hierárquica completa")
    ativo = models.BooleanField(default=True)
    data_atualizacao = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Secretaria Sinapse"
        verbose_name_plural = "Secretarias Sinapse"
        ordering = ['nome']
```

### Fase 2: Integração com API Sinapse

#### 2.1. Criar Serviço de Integração

**Arquivo:** `atendimentos/services/sinapse_api.py`

```python
import requests
from django.conf import settings

SINAPSE_API_BASE_URL = getattr(settings, 'SINAPSE_API_BASE_URL', 'https://api.mogidascruzes.sp.gov.br/api')
SINAPSE_API_TOKEN = getattr(settings, 'SINAPSE_API_TOKEN', None)

def buscar_estrutura_organizacional():
    """
    Busca estrutura organizacional completa da API Sinapse.
    Retorna lista de secretarias/órgãos com hierarquia.
    """
    # Implementar chamada à API Sinapse
    # Endpoint provável: /api/organograma/ ou /api/secretarias/
    pass

def buscar_secretaria_por_id(sinapse_id):
    """
    Busca uma secretaria específica por ID na API Sinapse.
    """
    pass
```

#### 2.2. Endpoint para Buscar Secretarias

**Nova View:** `BuscarSecretariasSinapseView`

```python
class BuscarSecretariasSinapseView(APIView):
    """
    Endpoint para buscar secretarias/órgãos da API Sinapse.
    Usado no frontend para preencher dropdown de encaminhamento.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        # Busca da API Sinapse ou cache local
        # Retorna lista formatada para o frontend
        pass
```

### Fase 3: Refatorar Serializer e View

#### 3.1. Atualizar `AtendimentoSerializer`

**Mudanças:**
- Remover `status` dos campos editáveis diretamente
- Criar método `update()` que valida mudança de status
- Exigir tramitação para mudar status

```python
class AtendimentoSerializer(serializers.ModelSerializer):
    # ... campos existentes ...
    
    # Status vira read-only (não pode ser alterado diretamente)
    status = serializers.CharField(read_only=True)
    
    def update(self, instance, validated_data):
        # Remove status se vier no validated_data
        validated_data.pop('status', None)
        
        # Atualiza outros campos normalmente
        instance = super().update(instance, validated_data)
        
        return instance
```

#### 3.2. Criar Novo Endpoint para Mudança de Status

**Nova View:** `AlterarStatusAtendimentoView`

```python
class AlterarStatusAtendimentoView(APIView):
    """
    Endpoint específico para alterar status via tramitação.
    Requer despacho obrigatório e dados de encaminhamento (se aplicável).
    """
    permission_classes = [permissions.IsAuthenticated, CanInteractWithAtendimento]
    
    def post(self, request, pk):
        """
        Altera status do atendimento criando uma tramitação.
        
        Payload esperado:
        {
            "status_novo": "ENCAMINHADO",
            "despacho": "Encaminhado para Secretaria de Educação...",
            "encaminhado_para_sinapse_id": 123,  # Opcional, obrigatório se status=ENCAMINHADO
            "encaminhado_para_nome": "Secretaria de Educação",  # Opcional
            "notificar_municipe": true  # Opcional
        }
        """
        atendimento = get_object_or_404(Atendimento, pk=pk)
        
        # Valida permissão
        # Valida transição de status (ex: não pode voltar de CONCLUIDO para ABERTO)
        # Cria tramitação com status
        # Atualiza status do atendimento
        # Envia notificação se solicitado
        pass
```

### Fase 4: Validações e Regras de Negócio

#### 4.1. Regras de Transição de Status

```python
TRANSICOES_PERMITIDAS = {
    'ABERTO': ['EM_ANALISE', 'ENCAMINHADO', 'ARQUIVADO'],
    'EM_ANALISE': ['ABERTO', 'ENCAMINHADO', 'CONCLUIDO', 'ARQUIVADO'],
    'ENCAMINHADO': ['EM_ANALISE', 'CONCLUIDO', 'ARQUIVADO'],
    'CONCLUIDO': ['ARQUIVADO'],  # Concluído só pode ir para Arquivado
    'ARQUIVADO': [],  # Arquivado é estado final
}

def validar_transicao_status(status_atual, status_novo):
    """
    Valida se a transição de status é permitida.
    """
    return status_novo in TRANSICOES_PERMITIDAS.get(status_atual, [])
```

#### 4.2. Validação para Status "Encaminhado"

```python
def validar_encaminhamento(status_novo, dados_encaminhamento):
    """
    Valida se status ENCAMINHADO tem dados de encaminhamento.
    """
    if status_novo == 'ENCAMINHADO':
        if not dados_encaminhamento.get('encaminhado_para_sinapse_id'):
            raise ValidationError(
                "Para status 'Encaminhado', é obrigatório informar o destino (Secretaria/Órgão)."
            )
    return True
```

### Fase 5: Frontend

#### 5.1. Alterar Formulário de Atendimento

- Remover campo de status do formulário de edição
- Adicionar botão "Alterar Status" que abre modal
- Modal deve ter:
  - Dropdown de novo status
  - Campo de despacho (obrigatório)
  - Campo de encaminhamento (se status=ENCAMINHADO)
  - Checkbox "Notificar munícipe"

#### 5.2. Integração com API Sinapse no Frontend

- Criar serviço `sinapse.js` para buscar secretarias
- Dropdown de encaminhamento busca da API Sinapse
- Cache local (localStorage) para melhor performance

## 📝 Estrutura de Arquivos a Criar/Modificar

### Criar:
1. `atendimentos/services/sinapse_api.py` - Serviço de integração
2. `atendimentos/models.py` - Atualizar modelo Tramitacao
3. `atendimentos/models.py` - Criar modelo SinapseSecretaria (opcional)
4. `atendimentos/views.py` - Nova view AlterarStatusAtendimentoView
5. `atendimentos/views.py` - Nova view BuscarSecretariasSinapseView
6. `atendimentos/serializers.py` - Atualizar TramitacaoSerializer
7. `atendimentos/serializers.py` - Atualizar AtendimentoSerializer
8. `atendimentos/management/commands/sincronizar_sinapse.py` - Comando para sincronizar cache
9. `frontend/src/services/sinapse.js` - Serviço frontend
10. `frontend/src/components/atendimentos/AlterarStatusModal.vue` - Componente modal

### Modificar:
1. `atendimentos/models.py` - Adicionar campos em Tramitacao
2. `atendimentos/serializers.py` - Tornar status read-only
3. `atendimentos/views.py` - Atualizar lógica de update
4. `atendimentos/urls.py` - Adicionar novas rotas
5. `core/settings.py` - Adicionar configurações Sinapse
6. `frontend/src/views/AtendimentoDetailView.vue` - Remover campo status, adicionar botão

## 🔄 Fluxo Proposto

### Antes (Atual):
```
Usuário → Edita Atendimento → Altera Status → Salva
         ↓ (opcional)
         Cria Tramitação
```

### Depois (Proposto):
```
Usuário → Clica "Alterar Status" → Preenche Modal:
         - Novo Status (obrigatório)
         - Despacho/Tramitação (obrigatório)
         - Encaminhamento (se ENCAMINHADO)
         → Salva
         ↓
         Sistema cria Tramitação automaticamente
         ↓
         Sistema atualiza Status do Atendimento
         ↓
         Sistema notifica munícipe (se solicitado)
```

## 📊 Benefícios

1. ✅ **Dados Qualitativos**: Toda mudança de status tem despacho explicativo
2. ✅ **Rastreabilidade**: Histórico completo de mudanças de status
3. ✅ **Integração Sinapse**: Encaminhamentos vinculados à estrutura organizacional oficial
4. ✅ **Auditoria**: Registro de quem mudou o status e quando
5. ✅ **Validação**: Regras de negócio garantem transições válidas

## 🚀 Implementação por Etapas

### Etapa 1: Modelo de Dados (Backend)
- [ ] Adicionar campos em Tramitacao
- [ ] Criar modelo SinapseSecretaria (opcional)
- [ ] Criar migração

### Etapa 2: Integração Sinapse (Backend)
- [ ] Criar serviço sinapse_api.py
- [ ] Criar view BuscarSecretariasSinapseView
- [ ] Criar comando de sincronização

### Etapa 3: Refatorar Status (Backend)
- [ ] Atualizar AtendimentoSerializer (status read-only)
- [ ] Criar AlterarStatusAtendimentoView
- [ ] Implementar validações de transição
- [ ] Atualizar URLs

### Etapa 4: Frontend
- [ ] Criar serviço sinapse.js
- [ ] Criar componente AlterarStatusModal.vue
- [ ] Atualizar AtendimentoDetailView.vue
- [ ] Remover campo status do formulário

### Etapa 5: Testes e Ajustes
- [ ] Testar fluxo completo
- [ ] Validar integração Sinapse
- [ ] Ajustar validações se necessário

## ⚠️ Considerações Importantes

1. **Migração de Dados**: Atendimentos existentes sem tramitação precisam ser tratados
2. **API Sinapse**: Verificar autenticação e endpoints disponíveis
3. **Performance**: Cache local de secretarias para evitar muitas chamadas à API
4. **Backward Compatibility**: Manter endpoints antigos temporariamente se necessário
5. **Notificações**: Manter sistema de notificação por e-mail funcionando

## 📚 Próximos Passos

1. Analisar documentação da API Sinapse (Swagger)
2. Definir endpoints específicos a usar
3. Criar modelo de dados
4. Implementar integração
5. Refatorar fluxo de status

---

**Data de Criação**: 10/02/2026  
**Status**: Planejamento
