from .settings import *  # noqa: F403,F401


# Banco isolado para testes automatizados (sem necessidade de CREATE DATABASE no MySQL).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Evita ruído de logs em arquivo durante execução de testes.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
}

