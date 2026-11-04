"""
Hilo en segundo plano que escucha al STM32 (o al simulador) todo el tiempo.

Por cada línea recibida:  protocolo.interpretar_linea  ->  procesador.procesar  ->  en_vivo
Además, cada 30 s revisa sensores sin datos y cada hora borra datos viejos.
"""
import logging
import os
import sys
import threading
import time

from django.db import close_old_connections
from django.utils import timezone

from . import en_vivo
from .models import Configuracion, Sensor
from .procesador import CuartoNoRegistrado, Procesador
from .protocolo import ErrorProtocolo, interpretar_linea
from .simulador import SimuladorSTM32

LOGGER = logging.getLogger(__name__)

SEGUNDOS_RECARGAR_CONFIG = 5
SEGUNDOS_REVISAR_SIN_DATOS = 30
SEGUNDOS_LIMPIEZA = 3600
MAX_BUFFER = 512

_lector = None
_lector_lock = threading.Lock()


class FuenteSerial:
    """Lee líneas completas del puerto serial donde está conectado el STM32."""

    def __init__(self, puerto, baudrate):
        import serial
        self._serial = serial.Serial(port=puerto, baudrate=baudrate, timeout=0.5)
        self._serial.reset_input_buffer()
        self._buffer = bytearray()

    def leer_linea(self):
        if b'\n' not in self._buffer:
            # Espera hasta 0.5 s a que llegue algo
            self._buffer += self._serial.read(self._serial.in_waiting or 1)
            if len(self._buffer) > MAX_BUFFER and b'\n' not in self._buffer:
                # Llegó mucha basura sin fin de línea (baudrate equivocado, por ejemplo)
                self._buffer.clear()
                raise ErrorProtocolo('Se recibieron más de 512 bytes sin fin de línea. ¿Baudrate correcto?')
        if b'\n' in self._buffer:
            linea, _, resto = self._buffer.partition(b'\n')
            self._buffer = bytearray(resto)
            return bytes(linea)
        return None

    def cerrar(self):
        self._serial.close()


class Lector(threading.Thread):
    def __init__(self):
        super().__init__(name='LectorCuartosFrios', daemon=True)
        self.procesador = Procesador()
        self._fuente = None
        self._firma_fuente = None
        self._config = None
        self._proxima_config = 0.0
        self._proxima_revision = time.monotonic() + SEGUNDOS_REVISAR_SIN_DATOS
        self._proxima_limpieza = time.monotonic() + 60
        self._cuartos_avisados = set()

    def run(self):
        time.sleep(1.0)  # dejar que Django termine de arrancar
        LOGGER.info("Lector de cuartos fríos iniciado.")
        try:
            cerradas = self.procesador.cerrar_pendientes_al_arrancar()
            if cerradas:
                LOGGER.info(f"Se cerraron {cerradas} excursiones/alertas que quedaron abiertas en la ejecución anterior.")
        except Exception:
            LOGGER.exception("No se pudieron cerrar los eventos pendientes de la ejecución anterior.")
        while True:
            try:
                self._ciclo()
            except Exception:
                LOGGER.exception("Error inesperado en el lector; reintentando en 2 s.")
                self._cerrar_fuente()
                time.sleep(2)

    def _ciclo(self):
        ahora = time.monotonic()
        if ahora >= self._proxima_config:
            close_old_connections()
            self._config = Configuracion.load()
            self._proxima_config = ahora + SEGUNDOS_RECARGAR_CONFIG
        self._tareas_periodicas(ahora)

        # Si cambió el modo, el puerto, el baudrate o los sensores simulados, se reabre la fuente
        cfg = self._config
        firma = (cfg.modo, cfg.puerto_serial, cfg.baudrate, cfg.sensores_simulados)
        if self._fuente is not None and firma != self._firma_fuente:
            LOGGER.info("Cambió la configuración del lector; reabriendo la fuente de datos.")
            self._cerrar_fuente()
        if self._fuente is None and not self._abrir_fuente(firma):
            time.sleep(2)
            return

        try:
            linea = self._fuente.leer_linea()
        except ErrorProtocolo as e:
            self._registrar_invalida(str(e))
            return
        except Exception as e:
            LOGGER.warning(f"Se perdió la conexión con {cfg.puerto_serial}: {e}")
            en_vivo.actualizar_lector(conectado=False, error=f"Conexión perdida: {e}")
            self._cerrar_fuente()
            time.sleep(1)
            return

        if linea:
            self._procesar_linea(linea)

    def _abrir_fuente(self, firma):
        modo, puerto, baudrate, sensores_simulados = firma
        try:
            if modo == Configuracion.MODO_SIMULADOR:
                self._fuente = SimuladorSTM32(sensores=sensores_simulados)
                puerto = 'simulador'
            else:
                self._fuente = FuenteSerial(puerto, baudrate)
        except Exception as e:
            error = f"No se pudo abrir {puerto}: {e}"
            if en_vivo.obtener_lector().get('error') != error:
                LOGGER.warning(f"{error}. Reintentando en segundo plano...")
            en_vivo.actualizar_lector(modo=modo, puerto=puerto, conectado=False, error=error)
            return False
        self._firma_fuente = firma
        LOGGER.info(f"Fuente de datos abierta: {puerto} (modo {modo}).")
        en_vivo.actualizar_lector(modo=modo, puerto=puerto, conectado=True, error=None)
        return True

    def _cerrar_fuente(self):
        if self._fuente is not None:
            try:
                self._fuente.cerrar()
            except Exception:
                pass
        self._fuente = None
        self._firma_fuente = None

    def _registrar_invalida(self, motivo):
        LOGGER.warning(f"Mensaje descartado: {motivo}")
        en_vivo.incrementar_lector('mensajes_invalidos')
        en_vivo.actualizar_lector(ultimo_error_protocolo=motivo)

    def _procesar_linea(self, linea):
        try:
            medicion = interpretar_linea(linea)
        except ErrorProtocolo as e:
            self._registrar_invalida(str(e))
            return

        try:
            resultado = self.procesador.procesar(medicion)
        except CuartoNoRegistrado as e:
            # Se avisa una sola vez por cuarto para no llenar la consola
            if medicion.cuarto not in self._cuartos_avisados:
                LOGGER.warning(f"{e} Regístralo en /admin/ para empezar a guardar sus datos.")
                self._cuartos_avisados.add(medicion.cuarto)
            self._registrar_invalida(str(e))
            return

        en_vivo.incrementar_lector('mensajes_validos')
        self._publicar_sensor(resultado.sensor)
        for alerta in resultado.alertas_nuevas:
            LOGGER.warning(f"ALERTA: {alerta.mensaje}")
        if resultado.alertas_nuevas or resultado.alertas_cerradas or resultado.excursiones_nuevas:
            en_vivo.alertas_cambiaron()

    def _publicar_sensor(self, sensor):
        en_vivo.actualizar_sensor(str(sensor), {
            'cuarto': sensor.cuarto.codigo,
            'sensor': sensor.codigo,
            'nombre': sensor.nombre,
            'en_linea': sensor.en_linea(),
            'temperatura': sensor.ultima_temperatura,
            'humedad': sensor.ultima_humedad,
            'fecha_hora': timezone.localtime(sensor.ultima_lectura).isoformat() if sensor.ultima_lectura else None,
            'estado': sensor.calcular_estado(),
        })

    def _tareas_periodicas(self, ahora):
        if ahora >= self._proxima_revision:
            self._proxima_revision = ahora + SEGUNDOS_REVISAR_SIN_DATOS
            nuevas = self.procesador.revisar_sin_datos()
            for alerta in nuevas:
                LOGGER.warning(f"ALERTA: {alerta.mensaje}")
                self._publicar_sensor(Sensor.objects.select_related('cuarto').get(pk=alerta.sensor_id))
            if nuevas:
                en_vivo.alertas_cambiaron()

        if ahora >= self._proxima_limpieza:
            self._proxima_limpieza = ahora + SEGUNDOS_LIMPIEZA
            borrados = self.procesador.limpiar()
            if any(borrados.values()):
                LOGGER.info(f"Limpieza de datos viejos: {borrados['lecturas']} lecturas, "
                            f"{borrados['excursiones']} excursiones y {borrados['alertas']} alertas borradas.")


def _debe_iniciar():
    # Con manage.py solo se arranca en el servidor, y solo en el proceso que atiende
    # (el autoreload de Django crea un proceso vigilante que no debe leer el puerto).
    argv = sys.argv
    if not any('manage.py' in arg for arg in argv):
        return True
    if not any(cmd in argv for cmd in ('runserver', 'runserver_plus')):
        return False
    if '--noreload' in argv:
        return True
    return os.environ.get('RUN_MAIN') == 'true' or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'


def iniciar_si_corresponde():
    global _lector
    with _lector_lock:
        if _lector is not None or not _debe_iniciar():
            return
        _lector = Lector()
        _lector.start()
