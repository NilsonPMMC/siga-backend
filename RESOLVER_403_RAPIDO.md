# 🚀 Solução Rápida: Erro 403 no Push

## ✅ Passo a Passo Rápido

### 1. Configurar usuário Git para este repositório:

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

git config user.name "NilsonPMMC"
git config user.email "seu-email-da-conta-NilsonPMMC@exemplo.com"
```

**⚠️ IMPORTANTE:** Substitua `seu-email-da-conta-NilsonPMMC@exemplo.com` pelo email real da conta NilsonPMMC no GitHub.

### 2. Criar Personal Access Token na conta NilsonPMMC:

1. **Acesse:** https://github.com/settings/tokens
   - Faça login como **NilsonPMMC** (use o navegador onde está logado como NilsonPMMC)

2. **Clique em:** "Generate new token" → "Generate new token (classic)"

3. **Configure:**
   - **Note**: `SIGA Backend - Push`
   - **Expiration**: Escolha um prazo (ex: 90 dias)
   - **Select scopes**: Marque **`repo`** (acesso completo aos repositórios)

4. **Clique em:** "Generate token"

5. **COPIE O TOKEN** (você só verá uma vez! Exemplo: `ghp_xxxxxxxxxxxxxxxxxxxx`)

### 3. Limpar credenciais antigas do Windows:

**Opção A - Via Interface Gráfica:**
1. Pressione `Win + R`
2. Digite: `control /name Microsoft.CredentialManager`
3. Vá em: **Credenciais do Windows**
4. Procure por: `git:https://github.com` ou `github.com`
5. **Remova** todas as entradas relacionadas ao GitHub

**Opção B - Via Linha de Comando:**
```bash
# Listar credenciais
cmdkey /list

# Remover credenciais do GitHub (se existirem)
cmdkey /delete:git:https://github.com
cmdkey /delete:LegacyGeneric:target=git:https://github.com
```

### 4. Fazer Push usando o Token:

```bash
cd c:\Users\23521\Documents\SIGA\SIGA\backend\siga-backend

git push origin main
```

**Quando pedir credenciais:**
- **Username**: `NilsonPMMC`
- **Password**: Cole o **TOKEN** que você copiou (não a senha do GitHub!)

## ✅ Verificação

Após o push bem-sucedido, você verá:
```
Enumerating objects: X, done.
Counting objects: 100% (X/X), done.
Writing objects: 100% (X/X), done.
To https://github.com/NilsonPMMC/siga-backend.git
   [hash]..[hash]  main -> main
```

## 🆘 Se Ainda Der Erro

### Verificar configuração:
```bash
# Ver usuário configurado
git config user.name
git config user.email

# Ver remote
git remote -v
```

### Tentar novamente:
1. Certifique-se de estar usando o **token** como senha (não a senha do GitHub)
2. Certifique-se de que o token tem escopo `repo`
3. Verifique se o token não expirou

## 📝 Dica: Salvar Token no Windows Credential Manager

Após fazer push com sucesso, o Windows pode perguntar se quer salvar as credenciais:
- **Marque "Salvar credenciais"** para não precisar digitar toda vez
- O Windows salvará o token automaticamente

## ✅ Pronto!

Após seguir esses passos, o push deve funcionar! 🚀
