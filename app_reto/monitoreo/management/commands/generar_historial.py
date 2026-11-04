from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from monitoreo.models import Alerta, Configuracion, Excursion, Lectura, Sensor
from monitoreo.procesador import CuartoNoRegistrado, Procesador
from monitoreo.protocolo import ErrorProtocolo, interpretar_linea
from monitoreo.simulador import SimuladorSTM32

PASO_SIMULACION_SEG = 5     # el simulador avanza como el STM32 real
PASO_PROCESO_SEG = 60       # pero solo se procesa un mensaje por minuto, para que sea rápido


class Command(BaseCommand):
    help = (
        "Llena la base de datos con varios días de datos SIMULADOS hasta ahora, pasando por el mismo "
        "procesador que los datos reales (genera lecturas, excursiones y alertas). Sirve para que la "
        "gráfica de 7 días y las estadísticas tengan datos en una demo."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=7)
        parser.add_argument('--semilla', type=int, default=None)
        parser.add_argument('--reemplazar', action='store_true',
                            help="Borra TODAS las lecturas, excursiones y alertas antes de generar.")

    def handle(self, *args, **options):
        dias = options['dias']
        if not 1 <= dias <= 60:
            raise CommandError("--dias debe estar entre 1 y 60.")
        ahora = timezone.now()
        inicio = ahora - timedelta(days=dias)

        if options['reemplazar']:
            Excursion.objects.all().delete()
            Alerta.objects.all().delete()
            Lectura.objects.all().delete()
            Sensor.objects.update(ultima_lectura=None, ultima_temperatura=None, ultima_humedad=None, ultimo_guardado=None)
        elif Lectura.objects.filter(fecha_hora__gte=inicio).exists():
            raise CommandError("Ya hay lecturas en ese periodo. Usa --reemplazar para borrar todo y generar de nuevo.")

        config = Configuracion.load()
        simulador = SimuladorSTM32(sensores=config.sensores_simulados, semilla=options['semilla'])
        procesador = Procesador(inicio=inicio)
        pasos_por_proceso = PASO_PROCESO_SEG // PASO_SIMULACION_SEG
        descartados = 0

        momento = inicio
        dia_reportado = -1
        while momento < ahora:
            with transaction.atomic():
                # Una hora simulada por transacción (mucho más rápido en SQLite)
                fin_bloque = min(momento + timedelta(hours=1), ahora)
                while momento < fin_bloque:
                    for _ in range(pasos_por_proceso):
                        lineas = simulador.avanzar(PASO_SIMULACION_SEG)
                    momento += timedelta(seconds=PASO_PROCESO_SEG)
                    for linea in lineas:
                        try:
                            procesador.procesar(interpretar_linea(linea), momento)
                        except (ErrorProtocolo, CuartoNoRegistrado):
                            descartados += 1
                    procesador.revisar_sin_datos(momento)

            dia = (momento - inicio).days
            if dia != dia_reportado and dia < dias:
                dia_reportado = dia
                self.stdout.write(f"  día {dia + 1} de {dias}...")

        self.stdout.write(self.style.SUCCESS(
            f"Listo: {Lectura.objects.count()} lecturas, {Excursion.objects.count()} excursiones "
            f"({Excursion.objects.filter(alerta__isnull=False).count()} largas) y {Alerta.objects.count()} alertas. "
            f"{descartados} mensajes dañados descartados por el checksum."
        ))
