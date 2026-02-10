# 🔐 Guia Seguro para Trocar SECRET_KEY em Produção

## ⚠️ IMPORTANTE: Leia antes de prosseguir!

Este guia explica como trocar a SECRET_KEY sem quebrar o sistema em produção.

## 📋 Impacto da Mudança da SECRET_KEY

### O que será afetado:

1. ✅ **Sessões Django** - Todas as sessões ativas serão invalidadas
   - Usuários precisarão fazer login novamente
   - **Não quebra o sistema**, apenas desconecta usuários

2. ✅ **Tokens JWT existentes** - Tokens antigos serão invalidados
   - Usuários precisarão fazer login novamente para obter novos tokens
   - **Não quebra o sistema**, apenas força renovação de tokens

3. ✅ **Cookies de sessão** - Serão invalidados
   - Usuários precisarão fazer login novamente
   - **Não quebra o sistema**

### O que NÃO será afetado:

- ✅ Dados do banco de dados (permanecem intactos)
- ✅ Estrutura do sistema (continua funcionando)
- ✅ Funcionalidades do sistema (apenas requer novo login)

## 🚨 ALERTA CRÍTICO DE SEGURANÇA

**Você compartilhou credenciais sensíveis no .env!**

As seguintes informações foram expostas:
- ❌ SECRET_KEY atual
- ❌ Senha do banco de dados MySQL
- ❌ Credenciais SMTP (usuário e senha)
- ❌ Google Client Secret
- ❌ Gemini API Key
- ❌ Credenciais SQL Server

**AÇÕES URGENTES NECESSÁRIAS:**

1. **Trocar TODAS as credenciais expostas** (não apenas SECRET_KEY)
2. **Gerar nova SECRET_KEY** (obrigatório)
3. **Trocar senha do banco de dados MySQL**
4. **Trocar senha SMTP**
5. **Regenerar Google OAuth credentials** (se possível)
6. **Regenerar Gemini API Key** (se possível)
7. **Trocar credenciais SQL Server**

## 🔄 Plano de Execução Seguro

### Fase 1: Preparação (ANTES do git pull)

1. **Gerar nova SECRET_KEY**:
   ```bash
   python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
   ```

2. **Escolher horário de baixo tráfego**:
   - Melhor: Madrugada ou fim de semana
   - Evitar: Horário comercial ativo

3. **Avisar usuários** (opcional mas recomendado):
   - "Manutenção programada: sistema ficará offline por 5-10 minutos"
   - "Após a manutenção, será necessário fazer login novamente"

### Fase 2: Execução (no servidor)

1. **Fazer backup do .env atual**:
   ```bash
   cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
   ```

2. **Editar o arquivo .env**:
   ```bash
   nano .env  # ou seu editor preferido
   ```

3. **Substituir SECRET_KEY**:
   ```env
   # ANTES:
   SECRET_KEY=django-insecure--7hk=jn*vw$wm*sd*6t=l0tkh(k5brj)_+un79yc)e9(805k4l
   
   # DEPOIS (cole a nova chave gerada):
   SECRET_KEY=sua-nova-chave-gerada-aqui
   ```

4. **Reiniciar serviços Django**:
   ```bash
   # Se usar systemd:
   sudo systemctl restart siga-backend
   
   # Se usar supervisor:
   sudo supervisorctl restart siga-backend
   
   # Se usar manualmente:
   # Pare o processo atual (Ctrl+C) e inicie novamente
   ```

5. **Verificar se está funcionando**:
   ```bash
   python manage.py check
   python manage.py verificar_seguranca
   ```

### Fase 3: Validação

1. **Testar login**:
   - Acesse a página de login
   - Faça login com um usuário
   - Verifique se funciona normalmente

2. **Verificar logs**:
   ```bash
   tail -f logs/debug.log
   # Procure por erros relacionados a SECRET_KEY ou autenticação
   ```

3. **Monitorar por alguns minutos**:
   - Verifique se não há erros
   - Confirme que usuários conseguem fazer login

## 🔐 Gerar Nova SECRET_KEY

### No servidor (recomendado):

```bash
cd /caminho/do/projeto/backend/siga-backend
python manage.py shell -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### Localmente (se tiver acesso):

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

**Copie a chave gerada e cole no arquivo .env**

## ✅ Checklist de Troca Segura

- [ ] Backup do .env atual criado
- [ ] Nova SECRET_KEY gerada
- [ ] Horário de baixo tráfego escolhido
- [ ] .env atualizado com nova SECRET_KEY
- [ ] Serviços Django reiniciados
- [ ] Sistema testado (login funciona)
- [ ] Logs verificados (sem erros)
- [ ] Sistema funcionando normalmente

## 🆘 Rollback (se algo der errado)

Se precisar voltar atrás:

1. **Restaurar .env antigo**:
   ```bash
   cp .env.backup.YYYYMMDD_HHMMSS .env
   ```

2. **Reiniciar serviços**:
   ```bash
   sudo systemctl restart siga-backend
   # ou
   sudo supervisorctl restart siga-backend
   ```

## 📝 Notas Importantes

1. **Não é necessário parar o servidor** - apenas reiniciar o processo Django
2. **Usuários serão desconectados** - isso é esperado e seguro
3. **Dados não serão perdidos** - apenas sessões serão invalidadas
4. **Tempo de indisponibilidade**: ~30 segundos a 2 minutos (tempo de reiniciar)

## 🔒 Próximos Passos Após Trocar SECRET_KEY

Após trocar a SECRET_KEY, você DEVE também trocar:

1. ✅ Senha do banco de dados MySQL
2. ✅ Credenciais SMTP
3. ✅ Google OAuth credentials (se possível)
4. ✅ Gemini API Key (se possível)
5. ✅ Credenciais SQL Server

Todas essas credenciais foram expostas e precisam ser alteradas por segurança.
