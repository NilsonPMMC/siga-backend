# 🔧 Corrigir: Erro de URL com Token

## ❌ Problema

O token contém caracteres que precisam ser codificados na URL. Vamos usar uma abordagem diferente.

## ✅ Solução: Usar Git Credential Helper

### Método 1: Configurar Credential Helper (Recomendado)

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Configurar para pedir credenciais sempre
git config --global credential.helper manager-core

# OU configurar apenas para este repositório
git config credential.helper manager-core

# Voltar remote ao normal (sem token na URL)
git remote set-url origin https://github.com/NilsonPMMC/siga-backend.git

# Fazer push (vai pedir credenciais)
git push origin main
```

Quando pedir credenciais:
- **Username**: `NilsonPMMC`
- **Password**: Cole o token que você criou (exemplo: `ghp_xxxxxxxxxxxxxxxxxxxx`)

---

## ✅ Método 2: Usar Variável de Ambiente GIT_ASKPASS

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Criar arquivo temporário com credenciais
echo @echo off > %TEMP%\git-askpass.bat
echo set /p=SEU_TOKEN_AQUI >> %TEMP%\git-askpass.bat

# Configurar variável de ambiente
set GIT_ASKPASS=%TEMP%\git-askpass.bat

# Voltar remote ao normal
git remote set-url origin https://github.com/NilsonPMMC/siga-backend.git

# Fazer push
git push origin main
```

---

## ✅ Método 3: Usar URL com Token Codificado (Mais Complexo)

Se quiser usar URL diretamente, precisa codificar o token:

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Token codificado (substitua pelo seu token codificado)
# Use uma ferramenta online para codificar: https://www.urlencoder.org/
# Ou use PowerShell:

# No PowerShell:
[System.Web.HttpUtility]::UrlEncode("SEU_TOKEN_AQUI")

# Depois use o token codificado na URL
git remote set-url origin https://NilsonPMMC:TOKEN_CODIFICADO@github.com/NilsonPMMC/siga-backend.git
```

---

## ✅ Método 4: Usar GitHub CLI (Mais Fácil)

Se você tem GitHub CLI instalado:

```bash
# Fazer login
gh auth login

# Selecionar:
# - GitHub.com
# - HTTPS
# - Login with a web browser
# - Escolher conta NilsonPMMC

# Depois fazer push normalmente
git push origin main
```

---

## ✅ Método 5: Push Direto com Credenciais (Mais Simples)

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Voltar remote ao normal
git remote set-url origin https://github.com/NilsonPMMC/siga-backend.git

# Fazer push (vai pedir credenciais)
git push origin main
```

Quando pedir:
- **Username**: `NilsonPMMC`
- **Password**: Cole o token que você criou

O Windows vai salvar automaticamente após o primeiro uso.

---

## 🚀 Solução Recomendada (Mais Simples)

**Use o Método 5** - é o mais simples e funciona bem:

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Garantir que remote está correto (sem token na URL)
git remote set-url origin https://github.com/NilsonPMMC/siga-backend.git

# Verificar
git remote -v

# Fazer push (vai pedir credenciais)
git push origin main
```

**Quando pedir credenciais:**
- Username: `NilsonPMMC`
- Password: Cole o token que você criou (não a senha do GitHub)

O Windows vai perguntar se quer salvar - **marque "Salvar"** para não precisar digitar toda vez.

---

## 🔍 Verificar Remote Atual

```bash
git remote -v
```

Deve mostrar:
```
origin  https://github.com/NilsonPMMC/siga-backend.git (fetch)
origin  https://github.com/NilsonPMMC/siga-backend.git (push)
```

**NÃO deve ter token na URL!**

---

## ✅ Pronto!

Use o **Método 5** - é o mais simples e funciona perfeitamente! 🚀
