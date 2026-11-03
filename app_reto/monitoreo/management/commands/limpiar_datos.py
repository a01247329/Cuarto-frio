from django.core.management.base import BaseCommand

from monitoreo.models import Configuracion
from monitoreo.procesador import Procesador


class Command(BaseCommand):
    help = "Borra ahora mismo las lecturas y alertas cerradas más viejas que los días de retención."

    def handle(self, *args, **options):
        dias = Configuracion.load().dias_retencion
        borrados = Procesador().limpiar()
        self.stdout.write(self.style.SUCCESS(
            f"Borradas {borrados['lecturas']} lecturas, {borrados['excursiones']} excursiones "
            f"y {borrados['alertas']} alertas con más de {dias} días."
        ))
