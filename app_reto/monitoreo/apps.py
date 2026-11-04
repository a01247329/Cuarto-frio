from django.apps import AppConfig


class MonitoreoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'monitoreo'
    verbose_name = 'Monitoreo de cuartos fríos'

    def ready(self):
        # Al arrancar el servidor se enciende el hilo que escucha al STM32
        from . import lector
        lector.iniciar_si_corresponde()
