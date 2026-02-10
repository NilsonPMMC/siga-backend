# 🚀 Guia Rápido: Trocar SECRET_KEY Sem Quebrar o Sistema

## ✅ Resposta Direta: É NECESSÁRIO e é SEGURO!

**Sim, você DEVE trocar a SECRET_KEY** porque:
1. Está usando o valor padrão inseguro (exposto no código)
2. Foi compartilhada publicamente (risco de segurança)

**É seguro trocar porque:**
- ✅ Não quebra o sistema
- ✅ Apenas desconecta usuários (eles fazem login novamente)
- ✅ Dados permanecem intactos
- ✅ Tempo de indisponibilidade: ~30 segundos

## 🔄 Passo a Passo Rápido

### 1. No servidor, gere a nova SECRET_KEY:

```bash
cd /caminho/do/projeto/backend/siga-backend

# Opção 1: Se tiver Django instalado
python manage.py shell -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# Opção 2: Use o script gerar_secret_key.py
python gerar_secret_key.py

# Opção 3: Python puro (sem Django)
python -c "import secrets; print(''.join(secrets.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for i in range(50)))"
```

### 2. Faça backup do .env atual:

```bash
cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
```

### 3. Edite o arquivo .env:

```bash
nano .env  # ou seu editor preferido
```

### 4. Substitua a SECRET_KEY:

```env
# ANTES:
SECRET_KEY=django-insecure--7hk=jn*vw$wm*sd*6t=l0tkh(k5brj)_+un79yc)e9(805k4l

# DEPOIS (cole a nova chave gerada):
SECRET_KEY=sua-nova-chave-aqui-gerada-no-passo-1
```

### 5. Reinicie o serviço Django:

```bash
# Se usar systemd:
sudo systemctl restart siga-backend

# Se usar supervisor:
sudo supervisorctl restart siga-backend

# Se rodar manualmente:
# Pare o processo (Ctrl+C) e inicie novamente: python manage.py runserver
```

### 6. Teste:

- Acesse a página de login
- Faça login com um usuário
- Se funcionar, está tudo OK! ✅

## ⚠️ O que vai acontecer:

1. **Usuários serão desconectados** - Isso é normal e esperado
2. **Eles precisarão fazer login novamente** - Sistema continua funcionando
3. **Tokens JWT antigos serão invalidados** - Novos tokens serão gerados no próximo login
4. **Dados não serão perdidos** - Tudo permanece intacto

## 🆘 Se algo der errado (rollback):

```bash
# Restaure o backup
cp .env.backup.YYYYMMDD_HHMMSS .env

# Reinicie o serviço
sudo systemctl restart siga-backend
```

## ⏰ Melhor Horário:

- **Recomendado**: Madrugada ou fim de semana (baixo tráfego)
- **Evitar**: Horário comercial ativo

## ✅ Checklist:

- [ ] Backup do .env criado
- [ ] Nova SECRET_KEY gerada
- [ ] .env atualizado com nova SECRET_KEY
- [ ] Serviço Django reiniciado
- [ ] Login testado e funcionando

## 🚨 ALERTA CRÍTICO:

Você compartilhou TODAS as credenciais do .env! Após trocar a SECRET_KEY, você DEVE também trocar:

1. ❌ **Senha do banco MySQL** (`DB_PASSWORD`)
2. ❌ **Credenciais SMTP** (`SMTP_USER`, `SMTP_PASSWORD`)
3. ❌ **Google OAuth** (`GOOGLE_CLIENT_SECRET`)
4. ❌ **Gemini API Key** (`GEMINI_API_KEY`)
5. ❌ **SQL Server** (`SQLSERVER_PASS`)

**Todas essas credenciais foram expostas e precisam ser alteradas!**

---

**Tempo estimado**: 5-10 minutos  
**Risco**: Baixo (apenas desconecta usuários)  
**Impacto**: Mínimo (sistema continua funcionando)
