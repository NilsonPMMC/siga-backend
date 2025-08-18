from django.apps import AppConfig

class EventosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'eventos'

    def ready(self):
        # Importa os sinais para que eles sejam registrados
        import eventos.signals
