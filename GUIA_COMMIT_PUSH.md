# 📤 Guia: Commit e Push das Correções de Segurança

## ⚠️ IMPORTANTE: Verificar ANTES de commitar!

**NUNCA commite o arquivo `.env`!** Ele contém credenciais sensíveis.

## 🔍 Passo 1: Verificar o que será commitado

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Ver status do Git
git status

# Ver diferenças (o que será commitado)
git diff
```

### ✅ Arquivos que DEVEM ser commitados:

- ✅ `core/settings.py` - Configurações atualizadas para usar variáveis de ambiente
- ✅ `.env.example` - Template de exemplo (SEM credenciais reais)
- ✅ `.gitignore` - Melhorado para proteger arquivos sensíveis
- ✅ `SEGURANCA.md` - Documentação de segurança
- ✅ `CORRECOES_SEGURANCA.md` - Resumo das correções
- ✅ `TROCA_SECRET_KEY.md` - Guia para trocar SECRET_KEY
- ✅ `GUIA_RAPIDO_TROCA_SECRET_KEY.md` - Guia rápido
- ✅ `PLANO_GIT_PULL_E_TROCA_SECRET_KEY.md` - Plano de execução
- ✅ `core/management/commands/verificar_seguranca.py` - Comando de verificação
- ✅ `gerar_secret_key.py` - Script para gerar SECRET_KEY

### ❌ Arquivos que NUNCA devem ser commitados:

- ❌ `.env` - Contém credenciais reais (JÁ está no .gitignore)
- ❌ `db.sqlite3` - Banco de dados local
- ❌ `logs/` - Arquivos de log
- ❌ `*.pyc` - Arquivos compilados Python
- ❌ `__pycache__/` - Cache Python

## 🔒 Passo 2: Verificar se .env está sendo ignorado

```bash
# Verificar se .env está no .gitignore
cat .gitignore | grep "\.env"

# Verificar se .env está sendo rastreado pelo Git
git ls-files | grep "\.env"
```

**Se `.env` aparecer em `git ls-files`, REMOVA do Git:**
```bash
git rm --cached .env
```

## 📝 Passo 3: Adicionar arquivos ao staging

```bash
# Adicionar arquivos específicos (RECOMENDADO)
git add core/settings.py
git add .env.example
git add .gitignore
git add SEGURANCA.md
git add CORRECOES_SEGURANCA.md
git add TROCA_SECRET_KEY.md
git add GUIA_RAPIDO_TROCA_SECRET_KEY.md
git add PLANO_GIT_PULL_E_TROCA_SECRET_KEY.md
git add core/management/commands/verificar_seguranca.py
git add gerar_secret_key.py

# OU adicionar todos os arquivos modificados (mais rápido, mas verifique antes!)
git add .
```

## 🔍 Passo 4: Verificar novamente o que será commitado

```bash
# Ver status após adicionar
git status

# Ver diferenças que serão commitadas
git diff --cached
```

**VERIFIQUE que `.env` NÃO está na lista!**

## 💾 Passo 5: Fazer commit

```bash
git commit -m "🔒 Correções de segurança: mover credenciais para variáveis de ambiente

- Movido SECRET_KEY para variável de ambiente
- Movidas credenciais do banco de dados para variáveis de ambiente
- Movida API Key do Gemini para variável de ambiente
- Configurado DEBUG via variável de ambiente
- Adicionadas validações de segurança
- Criado .env.example como template
- Melhorado .gitignore para proteger arquivos sensíveis
- Adicionado comando verificar_seguranca
- Criada documentação completa de segurança"
```

## 📤 Passo 6: Push para GitHub

```bash
# Verificar branch atual
git branch

# Push para GitHub
git push origin main
# ou
git push origin master
# (use o nome da sua branch principal)
```

## ✅ Passo 7: Verificar no GitHub

1. Acesse o repositório no GitHub
2. Verifique se o commit apareceu
3. **VERIFIQUE que o arquivo `.env` NÃO está no repositório!**
4. Verifique se `.env.example` está presente

## 🚨 Checklist Final

Antes de fazer push, verifique:

- [ ] `.env` NÃO está no `git status`
- [ ] `.env.example` está no `git status` (pode commitar)
- [ ] `core/settings.py` está no `git status`
- [ ] `.gitignore` está atualizado e inclui `.env`
- [ ] Nenhuma credencial real está sendo commitada
- [ ] Commit message está descritiva

## 🆘 Se cometer .env por engano

**AÇÃO IMEDIATA:**

```bash
# Remover .env do Git (mas manter no disco)
git rm --cached .env

# Fazer novo commit
git commit -m "Remover .env do controle de versão"

# Push
git push origin main
```

**⚠️ IMPORTANTE**: Se o `.env` já foi commitado e pushado:
1. As credenciais já estão expostas no histórico do Git
2. Você DEVE trocar TODAS as credenciais expostas
3. Considere usar `git filter-branch` ou `BFG Repo-Cleaner` para remover do histórico

## 📋 Resumo do Processo

```bash
# 1. Verificar status
git status

# 2. Verificar que .env não está sendo rastreado
git ls-files | grep "\.env"

# 3. Adicionar arquivos
git add core/settings.py .env.example .gitignore *.md core/management/commands/verificar_seguranca.py gerar_secret_key.py

# 4. Verificar novamente
git status

# 5. Commit
git commit -m "🔒 Correções de segurança: mover credenciais para variáveis de ambiente"

# 6. Push
git push origin main
```

## ✅ Pronto!

Após o push bem-sucedido, você pode fazer o `git pull` no servidor seguindo o `PLANO_GIT_PULL_E_TROCA_SECRET_KEY.md`.
