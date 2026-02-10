# 📋 Plano: Git Pull Agora + Troca SECRET_KEY no Fim do Dia

## ✅ SIM, você pode fazer git pull AGORA!

O código está preparado para ler do `.env`, então não vai quebrar o sistema.

## 🔄 Passo a Passo Completo

### FASE 1: AGORA - Git Pull (Seguro ✅)

#### 1. Verificar status atual:
```bash
cd /caminho/do/projeto/backend/siga-backend
git status
```

#### 2. Fazer backup do .env (por precaução):
```bash
cp .env .env.backup.antes.gitpull.$(date +%Y%m%d_%H%M%S)
```

#### 3. Verificar se há mudanças locais importantes:
```bash
git diff
# Se houver mudanças em settings.py que você fez manualmente, anote-as
```

#### 4. Fazer git pull:
```bash
git pull origin main
# ou
git pull origin master
# (use o nome da sua branch principal)
```

#### 5. Verificar se tudo está OK:
```bash
# Verificar se o Django carrega sem erros
python manage.py check

# Verificar configurações de segurança
python manage.py verificar_seguranca
```

#### 6. Se tudo OK, reiniciar o serviço:
```bash
sudo systemctl restart siga-backend
# ou
sudo supervisorctl restart siga-backend
```

#### 7. Testar rapidamente:
- Acesse o sistema no navegador
- Faça login
- Se funcionar, está tudo OK! ✅

**Resultado esperado**: Sistema continua funcionando normalmente, mas pode mostrar avisos sobre SECRET_KEY usando valor padrão (isso é esperado e será corrigido no fim do dia).

---

### FASE 2: FIM DO DIA - Trocar SECRET_KEY (Horário de Baixo Tráfego)

#### 1. Escolher horário adequado:
- ⏰ **Melhor**: Após 18h ou fim de semana
- ⏰ **Evitar**: Horário comercial (8h-18h)

#### 2. Avisar usuários (opcional mas recomendado):
```
"Manutenção programada às [HORA]: Sistema ficará offline por 5-10 minutos.
Após a manutenção, será necessário fazer login novamente."
```

#### 3. Gerar nova SECRET_KEY:
```bash
cd /caminho/do/projeto/backend/siga-backend

# Opção 1: Com Django
python manage.py shell -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# Opção 2: Script Python
python gerar_secret_key.py

# Opção 3: Python puro
python -c "import secrets; print(''.join(secrets.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for i in range(50)))"
```

**Copie a chave gerada!**

#### 4. Fazer backup do .env atual:
```bash
cp .env .env.backup.antes.troca.secretkey.$(date +%Y%m%d_%H%M%S)
```

#### 5. Editar o arquivo .env:
```bash
nano .env
# ou seu editor preferido
```

#### 6. Localizar e substituir SECRET_KEY:
```env
# ANTES:
SECRET_KEY=django-insecure--7hk=jn*vw$wm*sd*6t=l0tkh(k5brj)_+un79yc)e9(805k4l

# DEPOIS (cole a nova chave gerada no passo 3):
SECRET_KEY=sua-nova-chave-gerada-aqui
```

**Salve o arquivo** (Ctrl+O, Enter, Ctrl+X no nano)

#### 7. Verificar se está correto:
```bash
# Verificar se a SECRET_KEY foi alterada
grep "^SECRET_KEY=" .env

# Verificar configurações
python manage.py verificar_seguranca
```

#### 8. Reiniciar o serviço Django:
```bash
sudo systemctl restart siga-backend
# ou
sudo supervisorctl restart siga-backend
```

#### 9. Aguardar alguns segundos e testar:
```bash
# Verificar logs (opcional)
tail -f logs/debug.log

# No navegador:
# - Acesse o sistema
# - Faça login (usuários precisarão fazer login novamente)
# - Teste algumas funcionalidades básicas
```

#### 10. Se tudo OK, está concluído! ✅

---

## 📝 Checklist Completo

### AGORA (Git Pull):
- [ ] Backup do .env criado
- [ ] Git pull executado
- [ ] `python manage.py check` executado (sem erros)
- [ ] `python manage.py verificar_seguranca` executado
- [ ] Serviço reiniciado
- [ ] Sistema testado e funcionando

### FIM DO DIA (Troca SECRET_KEY):
- [ ] Horário de baixo tráfego escolhido
- [ ] Usuários avisados (opcional)
- [ ] Nova SECRET_KEY gerada
- [ ] Backup do .env criado
- [ ] .env atualizado com nova SECRET_KEY
- [ ] `python manage.py verificar_seguranca` executado
- [ ] Serviço reiniciado
- [ ] Login testado e funcionando
- [ ] Sistema funcionando normalmente

---

## ⚠️ O que esperar em cada fase:

### Após Git Pull (AGORA):
- ✅ Sistema continua funcionando
- ⚠️ Pode mostrar avisos sobre SECRET_KEY (isso é esperado)
- ✅ Nenhum usuário será desconectado
- ✅ Tudo funciona normalmente

### Após Trocar SECRET_KEY (FIM DO DIA):
- ✅ Sistema continua funcionando
- ✅ Avisos sobre SECRET_KEY desaparecem
- ⚠️ Usuários serão desconectados (precisarão fazer login novamente)
- ✅ Tokens JWT antigos invalidados (novos gerados no próximo login)
- ✅ Sistema mais seguro

---

## 🆘 Se algo der errado:

### Rollback Git Pull:
```bash
git log --oneline -5  # Ver últimos commits
git reset --hard HEAD~1  # Voltar 1 commit (CUIDADO!)
# ou
git reset --hard commit-anterior
```

### Rollback SECRET_KEY:
```bash
# Restaurar backup
cp .env.backup.antes.troca.secretkey.YYYYMMDD_HHMMSS .env

# Reiniciar serviço
sudo systemctl restart siga-backend
```

---

## 📞 Suporte

Se encontrar problemas:
1. Verifique os logs: `tail -f logs/debug.log`
2. Execute: `python manage.py check`
3. Execute: `python manage.py verificar_seguranca`
4. Verifique se o serviço está rodando: `sudo systemctl status siga-backend`

---

## ✅ Resumo Executivo

**AGORA**: 
- ✅ Git pull é seguro
- ✅ Sistema não será afetado
- ✅ Pode fazer normalmente

**FIM DO DIA**:
- ✅ Trocar SECRET_KEY é necessário e seguro
- ✅ Usuários serão desconectados (normal)
- ✅ Sistema continua funcionando
- ✅ Tempo estimado: 5-10 minutos

**Tudo pronto para prosseguir!** 🚀
