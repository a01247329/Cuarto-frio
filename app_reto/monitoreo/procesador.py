"""
Lógica central: recibe mediciones ya validadas y decide qué guardar y cuándo alertar.

No sabe nada del cable ni del simulador; eso lo hace lector.py. Gracias a esa
separación se puede probar con tests pasándole mediciones y horas inventadas.

Reglas:
  1. Cada mensaje actualiza el "último valor" del sensor.
  2. En el historial (Lectura) solo se guarda una fila cada `intervalo_guardado_seg`.
  3. Cada salida de rango, aunque dure segundos, se registra como Excursion.
  4. Si una excursión dura `minutos_para_alerta` minutos seguidos, además se registra
     una Alerta (abrir la puerta un momento no debe generar alertas).
  5. Cuando el valor regresa al rango, la excursión y la alerta se cierran (se les pone `fin`).
  6. Si un sensor deja de mandar datos `minutos_sin_datos`, se registra una alerta "sin datos".
  7. Las lecturas, excursiones y alertas cerradas con más de `dias_retencion` días se borran.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Alerta, Configuracion, CuartoFrio, Excursion, Lectura, Sensor, unidad_de

_DESCRIPCIONES = {
    Alerta.TEMP_ALTA: ('temperatura alta', 'máximo'),
    Alerta.TEMP_BAJA: ('temperatura baja', 'mínimo'),
    Alerta.HUM_ALTA: ('humedad alta', 'máximo'),
    Alerta.HUM_BAJA: ('humedad baja', 'mínimo'),
}


class CuartoNoRegistrado(Exception):
    """Llegó un mensaje de un cuarto que no existe en la base de datos o está inactivo."""


@dataclass
class Resultado:
    sensor: Sensor
    guardada: bool = False
    excursiones_nuevas: list = field(default_factory=list)
    alertas_nuevas: list = field(default_factory=list)
    alertas_cerradas: list = field(default_factory=list)


class Procesador:
    def __init__(self, inicio=None):
        self._inicio = inicio or timezone.now()

    def procesar(self, medicion, ahora=None):
        ahora = ahora or timezone.now()
        cuarto = CuartoFrio.objects.filter(codigo=medicion.cuarto, activo=True).first()
        if cuarto is None:
            raise CuartoNoRegistrado(f"El cuarto {medicion.cuarto!r} no está registrado o está inactivo.")

        with transaction.atomic():
            # Si llega un sensor nuevo de un cuarto conocido, se registra solo
            sensor, _ = Sensor.objects.get_or_create(cuarto=cuarto, codigo=medicion.sensor)
            resultado = Resultado(sensor=sensor)
            if not sensor.activo:
                return resultado

            config = Configuracion.load()
            sensor.ultima_lectura = ahora
            sensor.ultima_temperatura = medicion.temperatura
            sensor.ultima_humedad = medicion.humedad
            campos = ['ultima_lectura', 'ultima_temperatura', 'ultima_humedad']

            intervalo = timedelta(seconds=config.intervalo_guardado_seg)
            if sensor.ultimo_guardado is None or ahora - sensor.ultimo_guardado >= intervalo:
                Lectura.objects.create(
                    sensor=sensor, fecha_hora=ahora,
                    temperatura=medicion.temperatura, humedad=medicion.humedad,
                )
                sensor.ultimo_guardado = ahora
                campos.append('ultimo_guardado')
                resultado.guardada = True
            sensor.save(update_fields=campos)

            # Llegó un dato: si había alerta "sin datos", se cierra
            self._cerrar_alerta(sensor, Alerta.SIN_DATOS, ahora, resultado)
            self._evaluar_limites(sensor, cuarto, medicion, ahora, resultado)
        return resultado

    def _evaluar_limites(self, sensor, cuarto, medicion, ahora, resultado):
        reglas = [
            (medicion.temperatura, cuarto.temp_min, cuarto.temp_max, Alerta.TEMP_BAJA, Alerta.TEMP_ALTA),
            (medicion.humedad, cuarto.hum_min, cuarto.hum_max, Alerta.HUM_BAJA, Alerta.HUM_ALTA),
        ]
        for valor, minimo, maximo, tipo_bajo, tipo_alto in reglas:
            if valor is not None and valor > maximo:
                tipo_actual = tipo_alto
            elif valor is not None and valor < minimo:
                tipo_actual = tipo_bajo
            else:
                tipo_actual = None

            for tipo, limite in ((tipo_bajo, minimo), (tipo_alto, maximo)):
                if tipo == tipo_actual:
                    self._fuera_de_rango(sensor, cuarto, tipo, valor, limite, ahora, resultado)
                else:
                    # Regresó al rango: se cierran la excursión y la alerta si había
                    Excursion.objects.filter(sensor=sensor, tipo=tipo, fin__isnull=True).update(fin=ahora)
                    self._cerrar_alerta(sensor, tipo, ahora, resultado)

    def _fuera_de_rango(self, sensor, cuarto, tipo, valor, limite, ahora, resultado):
        excursion = Excursion.objects.filter(sensor=sensor, tipo=tipo, fin__isnull=True).first()
        if excursion is None:
            excursion = Excursion.objects.create(
                sensor=sensor, tipo=tipo, inicio=ahora, valor_limite=limite, valor_extremo=valor,
            )
            resultado.excursiones_nuevas.append(excursion)
        elif excursion.es_peor(valor):
            excursion.valor_extremo = valor
            excursion.save(update_fields=['valor_extremo'])

        abierta = Alerta.objects.filter(sensor=sensor, tipo=tipo, fin__isnull=True).first()
        if abierta:
            # Ya existe la alerta: solo se actualiza el peor valor
            if abierta.es_peor(valor):
                abierta.valor_extremo = valor
                abierta.save(update_fields=['valor_extremo'])
            return
        if ahora - excursion.inicio < timedelta(minutes=cuarto.minutos_para_alerta):
            return

        descripcion, tipo_limite = _DESCRIPCIONES[tipo]
        unidad = unidad_de(tipo)
        mensaje = (
            f"{cuarto.nombre} ({sensor}): {descripcion} durante {cuarto.minutos_para_alerta} min o más. "
            f"Valor: {excursion.valor_extremo:.1f} {unidad} ({tipo_limite} permitido: {limite:.1f} {unidad})."
        )
        alerta = Alerta.objects.create(
            sensor=sensor, tipo=tipo, inicio=excursion.inicio,
            valor_limite=limite, valor_extremo=excursion.valor_extremo, mensaje=mensaje,
        )
        excursion.alerta = alerta
        excursion.save(update_fields=['alerta'])
        resultado.alertas_nuevas.append(alerta)

    def _cerrar_alerta(self, sensor, tipo, ahora, resultado):
        abierta = Alerta.objects.filter(sensor=sensor, tipo=tipo, fin__isnull=True).first()
        if abierta:
            abierta.fin = ahora
            abierta.save(update_fields=['fin'])
            resultado.alertas_cerradas.append(abierta)

    def cerrar_pendientes_al_arrancar(self):
        """
        Al arrancar el programa no se sabe qué pasó mientras estuvo apagado, así que las
        excursiones y alertas de rango que quedaron abiertas se cierran en el último dato
        conocido. Si el problema sigue, se abrirán de nuevo con los datos que lleguen.
        """
        cerradas = 0
        for modelo in (Excursion, Alerta):
            pendientes = modelo.objects.filter(fin__isnull=True).exclude(tipo=Alerta.SIN_DATOS).select_related('sensor')
            for evento in pendientes:
                evento.fin = max(evento.sensor.ultima_lectura or evento.inicio, evento.inicio)
                evento.save(update_fields=['fin'])
                cerradas += 1
        return cerradas

    def revisar_sin_datos(self, ahora=None):
        """Registra alertas 'sin datos' de los sensores que dejaron de mandar. Regresa las nuevas."""
        ahora = ahora or timezone.now()
        limite = timedelta(minutes=Configuracion.load().minutos_sin_datos)
        nuevas = []
        for sensor in Sensor.objects.filter(activo=True, cuarto__activo=True).select_related('cuarto'):
            # Se cuenta desde que arrancó el programa, para no alertar por el tiempo que la PC estuvo apagada
            referencia = max(sensor.ultima_lectura or self._inicio, self._inicio)
            if ahora - referencia < limite:
                continue
            if Alerta.objects.filter(sensor=sensor, tipo=Alerta.SIN_DATOS, fin__isnull=True).exists():
                continue
            if sensor.ultima_lectura:
                ultimo = timezone.localtime(sensor.ultima_lectura).strftime('%d/%m/%Y %H:%M')
                detalle = f"último dato recibido el {ultimo}"
            else:
                detalle = "no ha enviado ningún dato desde que arrancó el programa"
            nuevas.append(Alerta.objects.create(
                sensor=sensor, tipo=Alerta.SIN_DATOS, inicio=referencia,
                mensaje=f"{sensor.cuarto.nombre} ({sensor}): sin datos, {detalle}. Revisar cable, STM32 o sensor.",
            ))
        return nuevas

    def limpiar(self, ahora=None):
        """Borra lecturas, excursiones y alertas cerradas más viejas que `dias_retencion`."""
        ahora = ahora or timezone.now()
        corte = ahora - timedelta(days=Configuracion.load().dias_retencion)
        lecturas, _ = Lectura.objects.filter(fecha_hora__lt=corte).delete()
        excursiones, _ = Excursion.objects.filter(fin__lt=corte).delete()
        alertas, _ = Alerta.objects.filter(fin__lt=corte).delete()
        return {'lecturas': lecturas, 'excursiones': excursiones, 'alertas': alertas}
