# 🔒 Guia de Segurança - SIGA

Este documento descreve as configurações de segurança implementadas e como configurá-las corretamente.

## ⚠️ IMPORTANTE: Configuração Inicial

### 1. Criar arquivo `.env`

O arquivo `.env` contém todas as credenciais sensíveis e **NUNCA** deve ser commitado no Git.

```bash
# Copie o arquivo de exemplo
cp .env.example .env

# Edite o arquivo .env com suas credenciais reais
nano .env  # ou use seu editor preferido
```

### 2. Variáveis Obrigatórias

Certifique-se de preencher **TODAS** as variáveis no arquivo `.env`:

#### 🔑 Críticas (Obrigatórias em Produção):
- `SECRET_KEY` - Chave secreta do Django
- `DB_PASSWORD` - Senha do banco de dados MySQL
- `SMTP_PASSWORD` - Senha do servidor SMTP

#### 📧 E-mail (Obrigatórias se usar envio de e-mails):
- `SMTP_USER` - Usuário SMTP
- `SMTP_PASSWORD` - Senha SMTP

#### 🔐 APIs Externas (Opcionais, mas recomendadas):
- `GOOGLE_CLIENT_ID` - Para integração com Google Calendar
- `GOOGLE_CLIENT_SECRET` - Para integração com Google Calendar
- `GEMINI_API_KEY` - Para funcionalidades de IA

## 🛡️ Configurações de Segurança

### Ambiente de Desenvolvimento

No arquivo `.env`, configure:
```env
DEBUG=True
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
```

### Ambiente de Produção

No arquivo `.env`, configure:
```env
DEBUG=False
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

## 🔐 Gerar Nova SECRET_KEY

Se precisar gerar uma nova SECRET_KEY segura:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Cole o resultado no arquivo `.env` na variável `SECRET_KEY`.

## ✅ Validações Implementadas

O sistema agora valida automaticamente:

1. ✅ **SECRET_KEY**: Emite aviso se estiver usando o valor padrão inseguro
2. ✅ **DEBUG**: Emite aviso se DEBUG=True em ambiente de produção
3. ✅ **DB_PASSWORD**: Bloqueia inicialização se senha estiver vazia em produção
4. ✅ **GEMINI_API_KEY**: Emite aviso se não estiver configurada

## 🚨 Checklist de Deploy em Produção

Antes de fazer deploy em produção, verifique:

- [ ] Arquivo `.env` criado e preenchido com valores reais
- [ ] `SECRET_KEY` alterada para um valor seguro único
- [ ] `DEBUG=False` configurado
- [ ] `DB_PASSWORD` configurada corretamente
- [ ] `SECURE_SSL_REDIRECT=True` configurado
- [ ] `SESSION_COOKIE_SECURE=True` configurado
- [ ] `CSRF_COOKIE_SECURE=True` configurado
- [ ] Arquivo `.env` **NÃO** está no Git (verifique com `git status`)
- [ ] Permissões do arquivo `.env` estão restritas (chmod 600)

## 📝 Exemplo de Arquivo .env para Produção

```env
# Django
SECRET_KEY=sua-chave-secreta-gerada-aqui
DEBUG=False
ALLOWED_HOSTS=gabinete.mogidascruzes.sp.gov.br
SITE_URL=https://gabinete.mogidascruzes.sp.gov.br

# Banco de Dados
DB_ENGINE=django.db.backends.mysql
DB_NAME=gabinete_db
DB_USER=gabinete_user
DB_PASSWORD=senha-segura-aqui
DB_HOST=localhost
DB_PORT=3306

# E-mail
EMAIL_HOST=cloud77.mailgrid.net.br
EMAIL_PORT=587
EMAIL_USE_TLS=True
SMTP_USER=usuario-smtp
SMTP_PASSWORD=senha-smtp
DEFAULT_FROM_EMAIL=comunicacao.gabinete@mogidascruzes.sp.gov.br

# Google API
GOOGLE_CLIENT_ID=seu-client-id
GOOGLE_CLIENT_SECRET=seu-client-secret

# Gemini AI
GEMINI_API_KEY=sua-api-key

# Celery
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# Segurança
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

## 🔍 Verificação de Segurança

Para verificar se tudo está configurado corretamente:

```bash
# Verifique se o .env existe
ls -la .env

# Verifique se o .env está no .gitignore (não deve aparecer)
git status

# Teste se o Django carrega sem erros
python manage.py check --deploy
```

## 📚 Referências

- [Django Security Checklist](https://docs.djangoproject.com/en/stable/howto/deployment/checklist/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [12 Factor App - Config](https://12factor.net/config)

## 🆘 Suporte

Se encontrar problemas de segurança ou tiver dúvidas, entre em contato com a equipe de TI.
