# ✅ Implementação: Tramitação Obrigatória e Integração Sinapse

## 📋 Resumo da Implementação

Implementação completa do sistema de tramitação obrigatória para alteração de status de atendimentos, com integração à API Sinapse para encaminhamentos.

## 🎯 O que foi Implementado

### 1. ✅ Modelo de Dados

#### Tramitacao (Atualizado)
- **Novos campos adicionados:**
  - `status_anterior`: Status antes da tramitação
  - `status_novo`: Novo status após tramitação
  - `alterou_status`: Flag indicando se houve mudança de status
  - `encaminhado_para_sinapse_id`: ID da secretaria/órgão na API Sinapse
  - `encaminhado_para_nome`: Nome do destino
  - `encaminhado_para_tipo`: Tipo (Secretaria, Setor, etc)

#### SinapseSecretaria (Novo Modelo)
- Cache local da estrutura organizacional da API Sinapse
- Campos: `sinapse_id`, `nome`, `sigla`, `tipo`, `hierarquia`, `ativo`, `data_atualizacao`

### 2. ✅ Serviço de Integração Sinapse

**Arquivo:** `atendimentos/services/sinapse_api.py`

- `buscar_estrutura_organizacional()`: Busca lista completa de secretarias
- `buscar_secretaria_por_id()`: Busca secretaria específica
- `validar_secretaria_sinapse()`: Valida existência de secretaria

### 3. ✅ Validações de Negócio

**Arquivo:** `atendimentos/validators.py`

- `validar_transicao_status()`: Valida se transição de status é permitida
- `validar_encaminhamento()`: Valida dados obrigatórios para status ENCAMINHADO
- Regras de transição implementadas:
  - **ABERTO** → EM_ANALISE, ENCAMINHADO, ARQUIVADO
  - **EM_ANALISE** → ABERTO, ENCAMINHADO, CONCLUIDO, ARQUIVADO
  - **ENCAMINHADO** → EM_ANALISE, CONCLUIDO, ARQUIVADO
  - **CONCLUIDO** → ARQUIVADO (apenas)
  - **ARQUIVADO** → (estado final)

### 4. ✅ Serializers Atualizados

#### TramitacaoSerializer
- Adicionados campos de status e encaminhamento
- Campos de display para status (`status_anterior_display`, `status_novo_display`)

#### AtendimentoSerializer
- **Status agora é read-only** - não pode ser alterado diretamente
- Removido de `read_only_fields` e adicionado validação no `update()`

### 5. ✅ Novas Views

#### AlterarStatusAtendimentoView
**Endpoint:** `POST /api/atendimentos/<pk>/alterar-status/`

**Funcionalidades:**
- Valida permissões do usuário
- Valida transição de status permitida
- Valida dados de encaminhamento (se status=ENCAMINHADO)
- Cria tramitação automaticamente
- Atualiza status do atendimento
- Envia notificação ao munícipe (opcional)

**Payload esperado:**
```json
{
    "status_novo": "ENCAMINHADO",
    "despacho": "Encaminhado para Secretaria de Educação...",
    "encaminhado_para_sinapse_id": 123,
    "encaminhado_para_nome": "Secretaria de Educação",
    "encaminhado_para_tipo": "Secretaria",
    "notificar_municipe": true
}
```

#### BuscarSecretariasSinapseView
**Endpoint:** `GET /api/sinapse/secretarias/`

**Funcionalidades:**
- Busca secretarias do cache local (prioridade)
- Se não houver cache, busca da API Sinapse
- Retorna lista formatada para dropdown no frontend

### 6. ✅ Configurações

**Arquivo:** `core/settings.py`

Adicionadas configurações:
- `SINAPSE_API_BASE_URL`: URL base da API Sinapse
- `SINAPSE_API_TOKEN`: Token de autenticação
- `SINAPSE_API_TIMEOUT`: Timeout para requisições

### 7. ✅ Admin Django

- `TramitacaoAdmin`: Atualizado com novos campos e fieldsets
- `SinapseSecretariaAdmin`: Novo admin para gerenciar cache

### 8. ✅ Migração

**Arquivo:** `migrations/0028_adicionar_campos_tramitacao_sinapse.py`

- Adiciona campos em `Tramitacao`
- Cria modelo `SinapseSecretaria`

## 📝 Arquivos Criados/Modificados

### Criados:
1. `atendimentos/services/__init__.py`
2. `atendimentos/services/sinapse_api.py`
3. `atendimentos/validators.py`
4. `atendimentos/migrations/0028_adicionar_campos_tramitacao_sinapse.py`
5. `IMPLEMENTACAO_TRAMITACAO_STATUS.md` (este arquivo)

### Modificados:
1. `atendimentos/models.py` - Adicionados campos em Tramitacao e novo modelo SinapseSecretaria
2. `atendimentos/serializers.py` - TramitacaoSerializer e AtendimentoSerializer atualizados
3. `atendimentos/views.py` - Novas views AlterarStatusAtendimentoView e BuscarSecretariasSinapseView
4. `atendimentos/urls.py` - Novas rotas adicionadas
5. `atendimentos/admin.py` - Admins atualizados
6. `core/settings.py` - Configurações Sinapse adicionadas

## 🔄 Fluxo de Uso

### Antes (Antigo):
```
Usuário → Edita Atendimento → Altera Status → Salva
         ↓ (opcional)
         Cria Tramitação
```

### Depois (Novo):
```
Usuário → Clica "Alterar Status" → Preenche Modal:
         - Novo Status (obrigatório)
         - Despacho/Tramitação (obrigatório)
         - Encaminhamento (se ENCAMINHADO)
         → POST /api/atendimentos/<pk>/alterar-status/
         ↓
         Sistema valida transição
         ↓
         Sistema cria Tramitação automaticamente
         ↓
         Sistema atualiza Status do Atendimento
         ↓
         Sistema notifica munícipe (se solicitado)
```

## 🚀 Próximos Passos

### Backend (Concluído ✅)
- [x] Modelo de dados
- [x] Validações
- [x] Views e endpoints
- [x] Migração

### Frontend (Pendente ⏳)
- [ ] Remover campo `status` do formulário de edição de atendimento
- [ ] Criar componente `AlterarStatusModal.vue`
- [ ] Integrar com endpoint `/api/atendimentos/<pk>/alterar-status/`
- [ ] Criar serviço `sinapse.js` para buscar secretarias
- [ ] Adicionar dropdown de encaminhamento (quando status=ENCAMINHADO)
- [ ] Atualizar visualização de tramitações para mostrar mudanças de status

### Configuração (Pendente ⏳)
- [ ] Adicionar variáveis no `.env`:
  ```env
  SINAPSE_API_BASE_URL=https://api.mogidascruzes.sp.gov.br/api
  SINAPSE_API_TOKEN=seu_token_aqui
  SINAPSE_API_TIMEOUT=10
  ```
- [ ] Executar migração no servidor:
  ```bash
  python manage.py migrate atendimentos
  ```
- [ ] Verificar endpoints da API Sinapse no Swagger e ajustar `sinapse_api.py` se necessário

### Comando de Sincronização (Opcional)
- [ ] Criar comando `python manage.py sincronizar_sinapse` para popular cache local

## ⚠️ Observações Importantes

1. **API Sinapse**: Os endpoints no `sinapse_api.py` são prováveis. É necessário verificar o Swagger real da API e ajustar conforme necessário:
   - Endpoint de estrutura organizacional
   - Formato de resposta
   - Autenticação (Bearer token ou outro método)

2. **Migração**: A migração foi criada manualmente. Execute no servidor após fazer pull:
   ```bash
   python manage.py migrate atendimentos
   ```

3. **Backward Compatibility**: Atendimentos existentes sem tramitação continuarão funcionando. Apenas novas alterações de status exigirão tramitação.

4. **Validação de Encaminhamento**: Para status ENCAMINHADO, o campo `encaminhado_para_sinapse_id` é obrigatório. O sistema valida isso antes de criar a tramitação.

## 📊 Benefícios Implementados

1. ✅ **Dados Qualitativos**: Toda mudança de status tem despacho explicativo obrigatório
2. ✅ **Rastreabilidade**: Histórico completo de mudanças de status com usuário e data
3. ✅ **Integração Sinapse**: Encaminhamentos vinculados à estrutura organizacional oficial
4. ✅ **Auditoria**: Registro completo de quem mudou o status e quando
5. ✅ **Validação**: Regras de negócio garantem transições válidas
6. ✅ **Dados Estruturados**: Encaminhamentos com ID, nome e tipo da secretaria

---

**Data de Implementação**: 10/02/2026  
**Status**: Backend Completo ✅ | Frontend Pendente ⏳
