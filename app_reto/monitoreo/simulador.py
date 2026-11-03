"""
Simulador del STM32: produce líneas con exactamente el mismo formato que mandará
el microcontrolador real, así el resto del programa no distingue datos reales de simulados.

Simula un cuarto frío con varios sensores (por defecto 8, como en la interfaz):
  - En reposo: ~8.5 °C y ~88 % de humedad. Cada sensor tiene su pequeña diferencia
    (no todos los rincones del cuarto están igual de fríos) y un poco de ruido.
  - Aperturas de puerta (en promedio cada 10 min, de 20 a 90 s): sube la temperatura,
    más en los sensores cercanos a la puerta, y luego se recupera.
  - Falla larga del compresor (en promedio cada 12 horas, de 15 a 22 min): sirve para ver alertas.
  - De vez en cuando un sensor deja de mandar datos unos minutos (se ve "Offline").
  - 1 de cada 100 mensajes llega dañado, para probar que el checksum los descarta.
"""
import math
import random
import time
from collections import deque

from .protocolo import construir_linea


class _SensorSimulado:
    def __init__(self, codigo, azar, reposo_t, reposo_h):
        self.codigo = codigo
        self.desfase_t = azar.gauss(0, 0.4)          # este rincón es un poco más frío o más caliente
        self.sensibilidad_puerta = azar.uniform(0.5, 1.2)
        self.temperatura = reposo_t + self.desfase_t
        self.humedad = reposo_h
        self.desconectado_restante = 0.0              # segundos que le faltan sin mandar datos


class SimuladorSTM32:
    TEMP_REPOSO = 8.5
    HUM_REPOSO = 88.0

    def __init__(self, cuarto='C01', sensores=8, cada_seg=5.0, semilla=None):
        self.cuarto = cuarto
        self.cada_seg = cada_seg
        self._azar = random.Random(semilla)
        self.sensores = [
            _SensorSimulado(f'S{i:02d}', self._azar, self.TEMP_REPOSO, self.HUM_REPOSO)
            for i in range(1, sensores + 1)
        ]
        self._puerta_restante = 0.0   # segundos que faltan para que se cierre la puerta
        self._falla_restante = 0.0    # segundos que faltan para que termine la falla
        self._pendientes = deque()
        self._proximo_envio = time.monotonic()

    def avanzar(self, dt):
        """Avanza la simulación `dt` segundos y regresa las líneas que mandaría el STM32 (una por sensor)."""
        azar = self._azar
        if self._puerta_restante <= 0 and azar.random() < dt / 600:
            self._puerta_restante = azar.uniform(20, 90)
        if self._falla_restante <= 0 and azar.random() < dt / 43200:
            self._falla_restante = azar.uniform(15 * 60, 22 * 60)
        self._puerta_restante = max(0.0, self._puerta_restante - dt)
        self._falla_restante = max(0.0, self._falla_restante - dt)

        # Cuánto se aleja el cuarto del reposo y qué tan rápido (tau en segundos)
        if self._falla_restante > 0:
            subida_t, objetivo_h, tau, por_puerta = 7.5, 82.0, 300.0, False
        elif self._puerta_restante > 0:
            subida_t, objetivo_h, tau, por_puerta = 8.5, 78.0, 60.0, True
        else:
            subida_t, objetivo_h, tau, por_puerta = 0.0, self.HUM_REPOSO, 120.0, False
        factor = 1 - math.exp(-dt / tau)

        lineas = []
        for sensor in self.sensores:
            subida = subida_t * sensor.sensibilidad_puerta if por_puerta else subida_t
            objetivo_t = self.TEMP_REPOSO + sensor.desfase_t + subida
            # Inercia térmica: el sensor se acerca al objetivo poco a poco, no de golpe
            sensor.temperatura += (objetivo_t - sensor.temperatura) * factor + azar.gauss(0, 0.03)
            sensor.humedad += (objetivo_h - sensor.humedad) * factor + azar.gauss(0, 0.2)
            sensor.humedad = min(100.0, max(0.0, sensor.humedad))

            if sensor.desconectado_restante <= 0 and azar.random() < dt / 14400:
                sensor.desconectado_restante = azar.uniform(20, 8 * 60)
            if sensor.desconectado_restante > 0:
                sensor.desconectado_restante -= dt
                continue

            linea = construir_linea(self.cuarto, sensor.codigo, sensor.temperatura, sensor.humedad)
            if azar.random() < 0.01:
                linea = self._danar(linea)
            lineas.append(linea)
        return lineas

    def _danar(self, linea):
        # Cambia un carácter, como haría el ruido eléctrico en el cable
        posicion = self._azar.randrange(1, len(linea) - 4)
        return linea[:posicion] + '#' + linea[posicion + 1:]

    def leer_linea(self, espera_max=0.5):
        """Se comporta como un puerto serial: regresa una línea si ya 'llegó' o None si aún no."""
        if self._pendientes:
            return self._pendientes.popleft()
        ahora = time.monotonic()
        if ahora < self._proximo_envio:
            time.sleep(min(espera_max, self._proximo_envio - ahora))
            return None
        self._proximo_envio = ahora + self.cada_seg
        self._pendientes.extend(self.avanzar(self.cada_seg))
        return self._pendientes.popleft() if self._pendientes else None

    def cerrar(self):
        pass
