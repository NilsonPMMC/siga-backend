# Roadmap — Módulo Atendimentos (unificação e assunto)

Documento de planejamento e **status de entrega**. Atualizado conforme as fases são concluídas.

**Decisão de produto:** todo registro operacional (visita, entrega de documento, demanda, etc.) passa a ser **Atendimento**. O modelo `RegistroVisita` deixa de ser o canal principal de entrada e será absorvido ou descontinuado após migração.

**Referências no código:**
- `Atendimento` — `atendimentos/models.py`
- `RegistroVisita` — `atendimentos/models.py`
- Automação de relatórios — `management/commands/enviar_relatorios_diarios_gestores.py`
- PDF atendimentos — `templates/atendimentos/relatorio_atendimentos.html`, `GerarPdfAtendimentosView`
- IA assunto — `atendimentos/services/assunto_ia.py`
- Triagem visita vs atendimento — removida na Fase 6 (`AtendimentoFormView.vue`)

---

## Visão geral das fases

| Fase | Escopo | Status |
|------|--------|--------|
| **1** | Cadastro auxiliar `Assunto` + campo em `Atendimento` | **Concluída** |
| **2** | API, Admin, formulários e listagens com assunto | **Concluída** |
| **3** | Automação LLM para preencher/sugerir assunto | **Concluída** |
| **4** | Unificar `RegistroVisita` → `Atendimento` + migração de dados | **Concluída** (backend) |
| **5** | Relatórios diários e PDF (sem check-in) + dashboard por assunto | **Concluída** |
| **6** | Frontend: remover triagem, limpar rotas/menus legados | **Concluída** |
| **7** | BI e Relatórios: UX por assunto (painel + PDF) | **Concluída** |

Ordem sugerida: **1 → 2 → 5 → 3 → 4 → 6** (fase 4 pode ser paralela à 5 após modelo estável).

---

## Fase 1 — Modelo de dados: Assunto ✅

- Modelo `AssuntoAtendimento` (nome, codigo, ativo, ordem)
- FK em `Atendimento`: `assunto`, `assunto_ia_sugerido`, `assunto_ia_status`
- Migration `0049_assunto_atendimento` + seed de 11 assuntos
- Admin CRUD de assuntos

---

## Fase 2 — Backend API e frontend com assunto ✅

| Item | Status |
|------|--------|
| `GET /api/assuntos-atendimento/` | ✅ |
| `AtendimentoSerializer` com assunto / filtros `?assunto_id=` | ✅ |
| `ATENDIMENTO_ASSUNTO_OBRIGATORIO` (`.env`, padrão `true`) | ✅ |
| Form, List, Detail com seletor/coluna/filtro de assunto | ✅ |

---

## Fase 3 — Automação LLM (assunto) ✅

### Entregue

| Componente | Descrição |
|------------|-----------|
| `services/assunto_ia.py` | Prompt com lista fechada de assuntos ativos; fallback `outros` |
| `POST /api/atendimentos/sugerir-assunto-preview/` | Sugestão antes de salvar (formulário) |
| `POST /api/atendimentos/{id}/sugerir-assunto/` | Sugestão manual; `?aplicar=true` para auto-aplicar |
| Pós-criação | Thread em background se `ATENDIMENTO_ASSUNTO_IA_POS_CRIACAO=true` (padrão) |
| `processar_ia_atendimentos` | Fila geral PENDENTE/ERRO (resumo + vetor + assunto) |
| `processar_ia_atendimentos_mes` | **Automação do mês** — backfill/cron por período (`YYYY-MM`) |
| `cron_processar_ia_atendimentos_mes.sh` | Script cron em `Documentos/sistema_gabinete/` |
| Settings | `ATENDIMENTO_ASSUNTO_IA_AUTO_APLICAR`, `ATENDIMENTO_ASSUNTO_IA_CONFIANCA_MINIMA` (0.85) |
| Frontend | Botão "Sugerir (IA)" no form; detalhe com sugestão pendente + "Aplicar" |

### Critérios de aceite

- ✅ Nunca inventa assunto fora da base — fallback `outros`
- ✅ Timeout/erro não bloqueia criação do atendimento (`assunto_ia_status=ERRO`)
- ✅ Edição manual do assunto marca `REVISADO`

---

## Fase 4 — Unificação `RegistroVisita` → `Atendimento` ✅ (backend)

| Item | Status |
|------|--------|
| FK `RegistroVisita.atendimento` (migration `0051`) | ✅ |
| `services/visita_atendimento.py` | ✅ |
| `migrar_visitas_para_atendimentos` (`--apply`) | ✅ executado (~1741 registros) |
| `POST /api/checkins/` cria Atendimento + vínculo legado | ✅ |
| Assunto padrão `visita_recepcao`, status `CONCLUIDO` | ✅ |

**Comando:** `python manage.py migrar_visitas_para_atendimentos --apply`

---

## Fase 5 — Relatórios e dashboard ✅

- Automação diária: apenas PDF de atendimentos (flags check-in removidas, migration `0050`)
- PDF: data/hora, munícipe + cargo/órgão, coluna assunto, big numbers por status e por assunto
- `GET /api/relatorios/atendimentos-por-assunto/`
- Dashboard: `atendimentos_do_dia`, cards por assunto hoje
- `RelatoriosView.vue`: gráfico e cards por assunto

---

## Fase 6 — Frontend ✅

| Item | Status |
|------|--------|
| Remover triagem `tipoRegistro` em `AtendimentoFormView.vue` | ✅ |
| **Remover agenda duplicada no Dashboard** (gestão só em Atendimentos) | ✅ |
| Redirect `/checkins` → `/atendimentos` (sem filtro de visita) | ✅ |
| Sem menu/atalho «Visitas / Recepção» — um único fluxo **Atendimentos** | ✅ |
| Assunto `visita_recepcao` permanece no cadastro (classificação), não como módulo separado | ✅ |
| `MunicipeDetailView`: aba check-in removida; histórico sem duplicar migrados | ✅ |

Endpoints `/api/checkins/` e `/api/dashboard/visitas/` mantidos no backend por compatibilidade; **UI não usa mais**.

---

## Fase 7 — BI e Relatórios visuais por assunto ✅

| Item | Status |
|------|--------|
| `GET /api/bi/atendimentos-por-assunto/` (filtros BI unificados em `Atendimento`) | ✅ |
| `BiAnalyticsView.vue`: KPIs, cards e gráfico por assunto; removidos blocos legados de visitas/check-in | ✅ |
| PDF BI (`relatorio_bi.html`): seção «Atendimentos por Assunto» | ✅ |
| `RelatoriosView.vue`: cards coloridos por assunto, grid de gráficos, destaque visual | ✅ |
| `visitas_hoje` removido do `DashboardSummary` (payload mais leve) | ✅ |

Endpoints legados `bi/visitas-*` permanecem deprecados; preferir `bi/atendimentos-por-assunto/`.

---

## Fase 8 — Mesclar `CategoriaAtendimento` → `Assunto` ✅

| Item | Status |
|------|--------|
| Migration `0052`: assuntos extras (cultura, esporte, elogio, ouvidoria, etc.) | ✅ |
| Serviço `mescla_categoria_assunto.py` + comando `mesclar_categorias_em_assuntos` | ✅ |
| Migração de dados: M2M categoria → FK assunto; backfill `OUTROS` nos sem assunto | ✅ |
| API: removidos `categorias` / `categorias_ids` do `AtendimentoSerializer` | ✅ |
| `GET /api/categorias/` e relatório por categoria → deprecated (lista vazia / delega assunto) | ✅ |
| Frontend: form, detalhe e relatórios sem categorias de atendimento | ✅ |

**Comando (produção):**

```bash
cd /var/www/gabinete/siga-gabinete && source venv/bin/activate
python manage.py migrate
python manage.py mesclar_categorias_em_assuntos   # dry-run
python manage.py mesclar_categorias_em_assuntos --apply --limpar-m2m --desativar-categorias --preencher-outros
```

O modelo `CategoriaAtendimento` e o M2M em `Atendimento` permanecem no banco (somente leitura histórica no Admin); a operação usa apenas **Assunto**.

---

## Pendências (pós-fases 1–8)

Itens **não bloqueiam** o uso diário unificado em Atendimentos; são limpeza técnica, operação ou evolução de modelo.

| Prioridade | Item | Situação |
|------------|------|----------|
| Baixa | Sunset `/api/checkins/`, `/api/dashboard/visitas/`, `bi/visitas-*`, PDF check-ins | Endpoints ativos; UI não usa |
| Baixa | Remover ou arquivar `CheckInHistoryView.vue` (órfão) | Arquivo existe; rota redireciona |
| Operação | Comunicar mudança à Recepção (um fluxo só «Atendimentos») | Checklist aberto |
| Operação | Plano de rollback da migração de visitas (backup + comando reverso) | Documentar se necessário |
| Opcional | Backfill IA assunto refinando `OUTROS` (pós-mescla) | `processar_ia_atendimentos_mes` |

### Melhoria recepção — busca de munícipe por CPF / matrícula RH ✅

| Item | Status |
|------|--------|
| `GET /api/municipes/lookup/?q=` inclui `cpf` e `matricula_rh` | ✅ |
| CPF com ou sem máscara (normaliza dígitos) | ✅ |
| `AtendimentoFormView`: placeholder e linha CPF/matrícula na lista | ✅ |
| Correção: termo com 11 dígitos não é mais tratado só como `id` | ✅ |

---

## Checklist de entregas (resumo)

### Backend
- [x] Modelo `AssuntoAtendimento` + Admin
- [x] FK `assunto` em `Atendimento` + migration (`0049`)
- [x] API assuntos + serializer + filtros + PDF
- [x] Validação `ATENDIMENTO_ASSUNTO_OBRIGATORIO`
- [x] Serviço + endpoints IA para sugerir assunto
- [x] Command migração `RegistroVisita` → `Atendimento`
- [ ] Deprecar endpoints `/api/checkins/` (sunset futuro; UI já usa só atendimentos)
- [x] Automação e-mail só PDF atendimentos
- [x] PDF e relatório/dashboard por assunto

### Frontend
- [x] Remover triagem visita/atendimento (Fase 6)
- [x] Seletor de assunto no form (obrigatório)
- [x] Listagem e detalhe com assunto
- [x] Botão sugerir assunto (IA) no form e detalhe
- [x] Dashboard e relatórios por assunto
- [x] Redirect `/checkins` → `/atendimentos`; sem UI separada para visitas
- [x] Dashboard sem agenda duplicada de visitas
- [x] BI e Relatórios com UX por assunto (Fase 7)

### Operação
- [x] Seed de assuntos (migration 0049)
- [ ] Comunicar mudança à Recepção
- [x] Cron/automação e-mail ajustado
- [x] Cron análise IA atendimentos do mês (`processar_ia_atendimentos_mes`)
- [ ] Plano de rollback da migração de visitas

---

## Variáveis de ambiente (assunto / IA)

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `ATENDIMENTO_ASSUNTO_OBRIGATORIO` | `true` | Exige assunto na criação/edição |
| `ATENDIMENTO_ASSUNTO_IA_POS_CRIACAO` | `true` | Dispara sugestão IA após criar atendimento |
| `ATENDIMENTO_ASSUNTO_IA_AUTO_APLICAR` | `false` | Aplica assunto automaticamente se confiança ≥ limiar |
| `ATENDIMENTO_ASSUNTO_IA_CONFIANCA_MINIMA` | `0.85` | Limiar para auto-aplicar |

Requer `OPENAI_API_KEY` + `OPENAI_API_BASE` (Groq) como o resumo IA existente.

### Automação — análise IA do mês corrente

Comando: `python manage.py processar_ia_atendimentos_mes`

| Flag | Descrição |
|------|-----------|
| `--referencia YYYY-MM` | Mês alvo (padrão: mês atual) |
| `--limite N` | Atendimentos por execução (padrão 100) |
| `--forcar` | Reprocessa todos do período |
| `--aplicar-assunto` | Preenche `assunto` quando confiança ≥ limiar |
| `--sem-aplicar-assunto` | Só grava sugestão (`assunto_ia_sugerido`) |
| `--dry-run` | Lista sem chamar IA |

**Backfill inicial (maio/2026, ~116 pendentes):**

```bash
cd /var/www/gabinete/siga-gabinete && source venv/bin/activate
python manage.py processar_ia_atendimentos_mes --referencia 2026-05 --aplicar-assunto --limite 200
# Repetir até "Nenhum atendimento a processar" ou usar --forcar --limite 500 uma vez
```

**Cron noturno:** `Documentos/sistema_gabinete/cron_processar_ia_atendimentos_mes.sh`  
Log: `/var/www/gabinete/logs/processar_ia_atendimentos_mes.log`

---

## Decisões de produto (registro 2026-05-21)

### 1. Assunto obrigatório na criação? → **Sim**

- Configuração: `ATENDIMENTO_ASSUNTO_OBRIGATORIO=true` (padrão no `.env`).
- API e formulário já validam; criação sem assunto retorna erro.

### 2. Status padrão das visitas migradas? → **`CONCLUIDO`**

- Migração já aplicada com status **Concluído** (presença na recepção = registro encerrado no dia).
- Configuração explícita: `ATENDIMENTO_VISITA_STATUS_PADRAO=CONCLUIDO` em `core/settings.py` (usado em `visita_atendimento.py` para novos vínculos legados via `/api/checkins/`).
- **Não é necessário re-migrar** só por causa do status; os atendimentos criados pelo comando já foram gravados assim.

### 3. `CategoriaAtendimento` vs `AssuntoAtendimento`? → **Mesclar (sem duplicidades)** — ✅ Fase 8

**Decisão aplicada:** taxonomia única por **Assunto** (FK obrigatória). Categorias de atendimento foram mapeadas para assuntos equivalentes, M2M limpo, cadastro de categorias desativado; API e UI não aceitam mais `categorias_ids`.

**Nota:** `CategoriaContato` (perfis de munícipes) é outro modelo e **não** foi alterado.

### 4. Campo `tipo_registro` (enum) vs inferir por assunto/título? → **Não criar enum (manter modelo atual)**

Pergunta do roadmap: vale a pena um campo fixo no banco, separado do assunto?

**Opção A — com enum `tipo_registro` (não implementado):**

```text
Atendimento #4521
  tipo_registro: VISITA          ← campo extra no modelo
  assunto: SAÚDE                 ← poderia até divergir do “tipo”
  titulo: Reunião com secretário
```

**Opção B — como está hoje (recomendado):**

```text
Atendimento #4521
  assunto: VISITA / RECEPÇÃO     ← codigo visita_recepcao
  titulo: VISITA — João Silva
  status: CONCLUIDO
```

Não existe `tipo_registro` no código. O “tipo” operacional (visita, demanda, etc.) é o **assunto** (+ título/descrição). A IA e os relatórios já usam `assunto.codigo`. Adicionar enum duplicaria informação e exigiria migration + sincronização com assunto.

### 5. Sunset de `RegistroVisita` e `/checkins`? → **Exemplo de cronograma sugerido**

Hoje ainda existem no backend (a UI não usa):

| Artefato | Função atual |
|----------|----------------|
| Modelo `RegistroVisita` | Tabela legada + FK `atendimento_id` |
| `POST/GET/PATCH/DELETE /api/checkins/` | Cria atendimento **e** linha legada |
| `GET /api/dashboard/visitas/` | Lista visitas do dia (formato antigo) |
| `GET /api/bi/visitas-*` | BI antigo sobre `RegistroVisita` |
| `GET /api/relatorios/checkins/pdf/` | PDF só de check-ins |

**Exemplo de fases de desligamento:**

| Quando | Ação |
|--------|------|
| **Agora** | Produção só via **Atendimentos**; links `/checkins` redirecionam |
| **+1–2 meses** | Marcar APIs legadas como deprecated (log + header `Deprecation`) |
| **+3 meses** | Desligar `POST /api/checkins/` (só leitura histórica) |
| **+6 meses** | Remover rotas BI/dashboard/PDF de visitas; tabela `RegistroVisita` somente leitura ou arquivada |
| **Depois** | Avaliar drop da tabela após backup e confirmação de que 100% têm `atendimento_id` |

Data exata pode ser alinhada com a equipe (ex.: «após 90 dias sem uso da API checkins nos logs»).

---

*Última atualização: 2026-05-21 — Fases 1–8 concluídas; mescla categorias → assunto aplicada via `mesclar_categorias_em_assuntos`.*
