# 🔧 Alternativas: Limpar Credenciais do Windows

## 🚀 Solução Mais Simples: Usar URL com Token Diretamente

Ao invés de limpar credenciais, você pode fazer push usando o token diretamente na URL:

### Passo 1: Criar Token (se ainda não criou)

1. Acesse: https://github.com/settings/tokens (logado como **NilsonPMMC**)
2. Crie um token com escopo `repo`
3. Copie o token (exemplo: `ghp_xxxxxxxxxxxxxxxxxxxx`)

### Passo 2: Fazer Push com Token na URL

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Substitua SEU_TOKEN_AQUI pelo token que você copiou
git push https://NilsonPMMC:SEU_TOKEN_AQUI@github.com/NilsonPMMC/siga-backend.git main
```

**Exemplo:**
```bash
git push https://NilsonPMMC:ghp_abc123xyz789@github.com/NilsonPMMC/siga-backend.git main
```

⚠️ **ATENÇÃO**: Este método expõe o token no histórico de comandos. Use apenas se necessário.

---

## 🔄 Alternativa 2: Usar Git Credential Manager

### Opção A - Via PowerShell (como Administrador):

```powershell
# Abrir PowerShell como Administrador
# Pressione Win + X e escolha "Windows PowerShell (Admin)"

# Remover credenciais específicas
git credential-manager-core erase
# Quando pedir, digite:
# protocol=https
# host=github.com
# (pressione Enter duas vezes)
```

### Opção B - Via CMD (como Administrador):

```cmd
# Abrir CMD como Administrador
# Pressione Win + X e escolha "Prompt de Comando (Admin)"

# Listar credenciais
cmdkey /list

# Remover credenciais do GitHub (substitua pelo nome exato que apareceu)
cmdkey /delete:git:https://github.com
```

---

## 🔐 Alternativa 3: Configurar Remote com Token (Mais Seguro)

### Passo 1: Criar Token

1. Acesse: https://github.com/settings/tokens (logado como **NilsonPMMC**)
2. Crie token com escopo `repo`
3. Copie o token

### Passo 2: Configurar Remote com Token

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

# Configurar remote com token (substitua SEU_TOKEN_AQUI)
git remote set-url origin https://NilsonPMMC:SEU_TOKEN_AQUI@github.com/NilsonPMMC/siga-backend.git

# Verificar
git remote -v

# Fazer push (não pedirá credenciais)
git push origin main
```

**⚠️ IMPORTANTE**: O token ficará visível no arquivo `.git/config`. Após fazer push, você pode remover o token:

```bash
# Remover token do remote (voltar ao normal)
git remote set-url origin https://github.com/NilsonPMMC/siga-backend.git
```

---

## 🛠️ Alternativa 4: Usar GitHub CLI (gh)

Se você tem GitHub CLI instalado:

```bash
# Fazer login na conta NilsonPMMC
gh auth login

# Selecionar GitHub.com
# Selecionar HTTPS
# Selecionar "Login with a web browser"
# Seguir instruções no navegador

# Depois fazer push normalmente
git push origin main
```

---

## 🔍 Alternativa 5: Verificar e Remover Manualmente

### Via Registry Editor:

1. Pressione `Win + R`
2. Digite: `regedit`
3. Navegue até:
   ```
   HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Credential Manager
   ```
4. Procure por entradas relacionadas ao GitHub
5. **CUIDADO**: Não delete nada sem saber o que está fazendo!

### Via PowerShell (Mais Seguro):

```powershell
# Listar credenciais do GitHub
[Windows.Security.Credentials.PasswordVault]::new().RetrieveAll() | Where-Object {$_.Resource -like "*github*"}

# Remover credenciais (execute com cuidado)
$vault = [Windows.Security.Credentials.PasswordVault]::new()
$vault.RetrieveAll() | Where-Object {$_.Resource -like "*github*"} | ForEach-Object {$vault.Remove($_)}
```

---

## ✅ Solução Recomendada (Mais Simples)

**Use a Alternativa 3** (configurar remote com token temporariamente):

```bash
# 1. Criar token em https://github.com/settings/tokens (conta NilsonPMMC)
# 2. Configurar remote com token
git remote set-url origin https://NilsonPMMC:SEU_TOKEN@github.com/NilsonPMMC/siga-backend.git

# 3. Fazer push
git push origin main

# 4. Remover token do remote (voltar ao normal)
git remote set-url origin https://github.com/NilsonPMMC/siga-backend.git
```

---

## 🆘 Se Nada Funcionar

Tente fazer push via interface gráfica:

1. **GitHub Desktop**: 
   - Abra o GitHub Desktop
   - Faça login como NilsonPMMC
   - Faça push pelo botão "Push origin"

2. **VS Code**:
   - Abra o VS Code
   - Use a extensão Git
   - Faça push pelo botão de sincronização

3. **GitKraken / SourceTree**:
   - Qualquer cliente Git gráfico que permita fazer login

---

## 📝 Resumo das Alternativas

| Método | Simplicidade | Segurança | Recomendado |
|--------|--------------|-----------|-------------|
| Token na URL (push direto) | ⭐⭐⭐⭐⭐ | ⭐⭐ | Para uso rápido |
| Remote com token | ⭐⭐⭐⭐ | ⭐⭐⭐ | **Recomendado** |
| GitHub CLI | ⭐⭐⭐ | ⭐⭐⭐⭐ | Se já tem instalado |
| Cliente gráfico | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | Mais fácil |

---

**Qual método você prefere tentar primeiro?** 🚀
