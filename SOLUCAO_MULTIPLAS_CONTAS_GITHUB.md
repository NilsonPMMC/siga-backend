# 🔧 Solução: Múltiplas Contas GitHub

## 📋 Situação

Você tem duas contas GitHub:
- **NilsonPMMC** - Gerencia `siga-backend` e outros projetos
- **Nilson1308** - Gerencia outros projetos

O Git está usando credenciais da conta errada (Nilson1308) para o repositório siga-backend.

## ✅ Solução: Configurar Conta Específica por Repositório

### Opção 1: Configurar Usuário/Email Apenas para Este Repositório (Recomendado)

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Configurar usuário e email apenas para este repositório
git config user.name "NilsonPMMC"
git config user.email "seu-email-da-conta-NilsonPMMC@exemplo.com"

# Verificar
git config user.name
git config user.email
```

**Substitua `seu-email-da-conta-NilsonPMMC@exemplo.com` pelo email real da conta NilsonPMMC no GitHub.**

### Opção 2: Usar Personal Access Token (Mais Seguro)

1. **Criar Token na Conta NilsonPMMC:**
   - Acesse: https://github.com/settings/tokens
   - Faça login como **NilsonPMMC**
   - Clique em "Generate new token" → "Generate new token (classic)"
   - Dê um nome: "SIGA Backend Local"
   - Selecione escopo: **`repo`** (acesso completo aos repositórios)
   - Clique em "Generate token"
   - **COPIE O TOKEN** (você só verá uma vez!)

2. **Fazer Push com Token:**
   ```bash
   cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend
   
   git push origin main
   ```
   
   Quando pedir credenciais:
   - **Username**: `NilsonPMMC`
   - **Password**: Cole o **token** que você copiou (não a senha do GitHub!)

### Opção 3: Usar SSH com Chave Específica (Mais Avançado)

Se você já tem SSH configurado, pode usar uma chave específica:

```bash
# Configurar SSH para usar chave específica da conta NilsonPMMC
git config core.sshCommand "ssh -i ~/.ssh/id_rsa_nilsonpmmc"

# Ou configurar no arquivo ~/.ssh/config
```

## 🔍 Verificar Configuração Atual

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Ver configuração local (apenas este repositório)
git config --local --list

# Ver configuração global (todos os repositórios)
git config --global --list
```

## 🚀 Passo a Passo Recomendado

### 1. Configurar usuário/email para este repositório:

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

git config user.name "NilsonPMMC"
git config user.email "seu-email@exemplo.com"  # Email da conta NilsonPMMC
```

### 2. Limpar credenciais antigas do Windows:

1. Abra: **Painel de Controle** → **Gerenciador de Credenciais**
2. Vá em: **Credenciais do Windows**
3. Procure por: `git:https://github.com`
4. **Remova** as credenciais antigas do GitHub

### 3. Criar Personal Access Token:

1. Acesse: https://github.com/settings/tokens (logado como **NilsonPMMC**)
2. Crie um token com escopo `repo`
3. Copie o token

### 4. Fazer Push:

```bash
git push origin main
```

Quando pedir credenciais:
- **Username**: `NilsonPMMC`
- **Password**: Cole o **token** (não a senha!)

## 📝 Configuração Permanente (Opcional)

Se quiser que este repositório sempre use a conta NilsonPMMC:

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Configurar URL do remote com usuário
git remote set-url origin https://NilsonPMMC@github.com/NilsonPMMC/siga-backend.git

# Ou manter sem usuário (mais seguro, pedirá credenciais)
git remote set-url origin https://github.com/NilsonPMMC/siga-backend.git
```

## ✅ Verificação Final

```bash
# Verificar remote
git remote -v

# Verificar usuário configurado
git config user.name

# Tentar push
git push origin main
```

## 🆘 Se Ainda Der Erro 403

1. **Verifique se está logado na conta certa:**
   ```bash
   # Ver qual conta está sendo usada
   git config user.name
   ```

2. **Limpe cache de credenciais:**
   ```bash
   git credential-manager-core erase
   # ou no Windows:
   cmdkey /list
   cmdkey /delete:git:https://github.com
   ```

3. **Use token ao invés de senha:**
   - Sempre use Personal Access Token, nunca a senha do GitHub

## 📚 Dica: Gerenciar Múltiplas Contas

Para facilitar no futuro, você pode:

1. **Usar SSH keys diferentes** para cada conta
2. **Configurar arquivo `~/.ssh/config`** para usar chaves diferentes por repositório
3. **Usar GitHub CLI** (`gh`) que gerencia múltiplas contas melhor

## ✅ Resumo

1. Configure usuário/email para este repositório: `git config user.name "NilsonPMMC"`
2. Crie Personal Access Token na conta NilsonPMMC
3. Limpe credenciais antigas do Windows
4. Faça push usando o token como senha

**Tudo pronto!** 🚀
