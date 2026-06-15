from django.apps import AppConfig


class AtendimentosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'atendimentos'

    def ready(self):
        import atendimentos.signals  # noqa: F401
        import atendimentos.signals_crm  # noqa: F401
        import atendimentos.signals_sla  # noqa: F401
