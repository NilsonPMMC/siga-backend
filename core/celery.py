import os
from celery import Celery

# Define a variável de ambiente para que o Celery saiba onde encontrar as configurações do Django.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# Cria a instância do aplicativo Celery. O nome 'core' é o nome do seu projeto.
app = Celery('core')

# O Celery vai carregar suas configurações a partir do seu arquivo settings.py do Django.
# O namespace='CELERY' significa que todas as configurações do Celery devem começar com CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# O Celery vai procurar automaticamente por tarefas (em arquivos tasks.py) em todos os seus apps instalados.
app.autodiscover_tasks()