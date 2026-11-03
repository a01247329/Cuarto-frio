from django.core.management.base import BaseCommand

from monitoreo.protocolo import ErrorProtocolo, interpretar_linea
from monitoreo.simulador import SimuladorSTM32


class Command(BaseCommand):
    help = "Muestra líneas como las que mandará el STM32, para ver el formato del protocolo."

    def add_arguments(self, parser):
        parser.add_argument('--ciclos', type=int, default=2, help="Cuántas rondas de mensajes (una línea por sensor).")
        parser.add_argument('--sensores', type=int, default=8)
        parser.add_argument('--cada', type=float, default=5.0, help="Segundos simulados entre rondas.")
        parser.add_argument('--semilla', type=int, default=None)

    def handle(self, *args, **options):
        simulador = SimuladorSTM32(sensores=options['sensores'], semilla=options['semilla'])
        lineas = [l.strip() for _ in range(options['ciclos']) for l in simulador.avanzar(options['cada'])]
        for linea in lineas:
            try:
                m = interpretar_linea(linea)
                humedad = f"{m.humedad:.1f} %" if m.humedad is not None else "sin humedad"
                self.stdout.write(f"{linea:<28} -> {m.cuarto}/{m.sensor}: {m.temperatura:.2f} °C, {humedad}")
            except ErrorProtocolo as e:
                self.stdout.write(self.style.WARNING(f"{linea:<28} -> DESCARTADA: {e}"))
