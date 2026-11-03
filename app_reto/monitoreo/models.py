"""
Tablas de la base de datos.

    CuartoFrio  1 ──< Sensor 1 ──< Lectura
                                ├──< Excursion  (cada salida de rango, aunque dure segundos)
                                └──< Alerta     (salida de rango que duró demasiado, o sensor sin datos)
    Configuracion (una sola fila, igual que PesajeSetting en la báscula)

Un cuarto tiene varios sensores; cada sensor tiene muchas lecturas, excursiones y alertas.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


# Estados posibles de un sensor o cuarto, del menos al más grave
ESTADO_NORMAL = 'normal'
ESTADO_ADVERTENCIA = 'advertencia'         # dentro del rango, pero pasó el límite de advertencia
ESTADO_FUERA_DE_RANGO = 'fuera_de_rango'   # fuera de límites, pero aún no cumple el tiempo para alertar
ESTADO_SIN_DATOS = 'sin_datos'
ESTADO_ALERTA = 'alerta'
ORDEN_ESTADOS = [ESTADO_NORMAL, ESTADO_ADVERTENCIA, ESTADO_FUERA_DE_RANGO, ESTADO_SIN_DATOS, ESTADO_ALERTA]

# Si el último dato de un sensor tiene más de esto, se muestra como "Offline".
# Es solo visual: la alerta "sin datos" usa Configuracion.minutos_sin_datos.
SEGUNDOS_EN_LINEA = 30


def peor_estado(estados):
    estados = list(estados)
    if not estados:
        return ESTADO_SIN_DATOS
    return max(estados, key=ORDEN_ESTADOS.index)


# Tipos de salida de rango (los usan Excursion y Alerta)
TEMP_ALTA = 'temp_alta'
TEMP_BAJA = 'temp_baja'
HUM_ALTA = 'hum_alta'
HUM_BAJA = 'hum_baja'
SIN_DATOS = 'sin_datos'
TIPOS_FUERA_DE_RANGO = [
    (TEMP_ALTA, 'Temperatura alta'),
    (TEMP_BAJA, 'Temperatura baja'),
    (HUM_ALTA, 'Humedad alta'),
    (HUM_BAJA, 'Humedad baja'),
]


def es_peor_valor(tipo, extremo_actual, valor):
    if extremo_actual is None:
        return True
    if tipo in (TEMP_ALTA, HUM_ALTA):
        return valor > extremo_actual
    return valor < extremo_actual


def unidad_de(tipo):
    return '%' if tipo in (HUM_ALTA, HUM_BAJA) else '°C'


class Configuracion(models.Model):
    # Patrón Singleton: una única fila de configuración global
    MODO_SIMULADOR = 'simulador'
    MODO_SERIAL = 'serial'

    singleton_id = models.IntegerField(default=1, primary_key=True, editable=False)

    modo = models.CharField(
        max_length=20,
        choices=[(MODO_SIMULADOR, 'Simulador (sin hardware)'), (MODO_SERIAL, 'Puerto serial (STM32 real)')],
        default=MODO_SIMULADOR,
        help_text="De dónde salen los datos: del simulador o del STM32 por el puerto serial.",
    )
    puerto_serial = models.CharField(max_length=50, default='COM3', help_text="Puerto del STM32 (COM3 en Windows, /dev/ttyACM0 en Linux).")
    baudrate = models.PositiveIntegerField(default=115200, validators=[MinValueValidator(300)], help_text="Velocidad del puerto serial.")
    intervalo_guardado_seg = models.PositiveIntegerField(
        default=60, validators=[MinValueValidator(1)],
        help_text="Cada cuántos segundos se guarda una lectura por sensor en el historial.",
    )
    dias_retencion = models.PositiveIntegerField(
        default=60, validators=[MinValueValidator(1)],
        help_text="Días que se conservan las lecturas antes de borrarse automáticamente.",
    )
    minutos_sin_datos = models.PositiveIntegerField(
        default=5, validators=[MinValueValidator(1)],
        help_text="Minutos sin recibir datos de un sensor para generar la alerta 'sin datos'.",
    )
    sensores_simulados = models.PositiveIntegerField(
        default=8, validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text="Cuántos sensores genera el simulador (solo en modo simulador).",
    )

    def __str__(self):
        return "Configuración del monitoreo"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    class Meta:
        verbose_name = "Configuración"
        verbose_name_plural = "Configuración"


class CuartoFrio(models.Model):
    codigo = models.CharField(max_length=10, unique=True, help_text="Código que manda el STM32 (ej. C01).")
    nombre = models.CharField(max_length=100, help_text="Nombre para mostrar (ej. Cuarto frío 1).")
    temp_min = models.FloatField(default=2.0, help_text="Temperatura mínima permitida en °C.")
    temp_max = models.FloatField(default=8.0, help_text="Temperatura máxima permitida en °C.")
    temp_advertencia = models.FloatField(
        null=True, blank=True,
        help_text="Desde esta temperatura el sensor se muestra en advertencia (amarillo), antes de llegar al máximo. Vacío = sin advertencia.",
    )
    hum_min = models.FloatField(default=80.0, help_text="Humedad mínima permitida en %.")
    hum_max = models.FloatField(default=95.0, help_text="Humedad máxima permitida en %.")
    minutos_para_alerta = models.PositiveIntegerField(
        default=10,
        help_text="Minutos seguidos fuera de rango antes de registrar una alerta (evita alertas al abrir la puerta).",
    )
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.nombre} ({self.codigo})"

    def clean(self):
        if self.temp_min >= self.temp_max:
            raise ValidationError({'temp_max': "La temperatura máxima debe ser mayor que la mínima."})
        if self.temp_advertencia is not None and not (self.temp_min < self.temp_advertencia < self.temp_max):
            raise ValidationError({'temp_advertencia': "La advertencia debe estar entre la temperatura mínima y la máxima."})
        if self.hum_min >= self.hum_max:
            raise ValidationError({'hum_max': "La humedad máxima debe ser mayor que la mínima."})

    def fuera_de_rango(self, temperatura, humedad):
        if temperatura is not None and not (self.temp_min <= temperatura <= self.temp_max):
            return True
        if humedad is not None and not (self.hum_min <= humedad <= self.hum_max):
            return True
        return False

    def en_advertencia(self, temperatura):
        return self.temp_advertencia is not None and temperatura is not None and temperatura >= self.temp_advertencia

    class Meta:
        ordering = ['codigo']
        verbose_name = "Cuarto frío"
        verbose_name_plural = "Cuartos fríos"


class Sensor(models.Model):
    cuarto = models.ForeignKey(CuartoFrio, on_delete=models.CASCADE, related_name='sensores')
    codigo = models.CharField(max_length=10, help_text="Código que manda el STM32 (ej. S01).")
    descripcion = models.CharField(max_length=200, blank=True, help_text="Ubicación u otra nota (ej. junto a la puerta).")
    activo = models.BooleanField(default=True)

    # Último valor recibido (se actualiza con cada mensaje, se guarde o no en el historial)
    ultima_lectura = models.DateTimeField(null=True, blank=True)
    ultima_temperatura = models.FloatField(null=True, blank=True)
    ultima_humedad = models.FloatField(null=True, blank=True)
    # Momento de la última fila guardada en Lectura, para respetar el intervalo de guardado
    ultimo_guardado = models.DateTimeField(null=True, blank=True, editable=False)

    def __str__(self):
        return f"{self.cuarto.codigo}/{self.codigo}"

    @property
    def nombre(self):
        return self.descripcion or self.codigo

    def en_linea(self, ahora=None):
        if self.ultima_lectura is None:
            return False
        ahora = ahora or timezone.now()
        return ahora - self.ultima_lectura <= timedelta(seconds=SEGUNDOS_EN_LINEA)

    def calcular_estado(self):
        tipos_abiertos = set(self.alertas.filter(fin__isnull=True).values_list('tipo', flat=True))
        if Alerta.SIN_DATOS in tipos_abiertos or self.ultima_lectura is None:
            return ESTADO_SIN_DATOS
        if tipos_abiertos:
            return ESTADO_ALERTA
        if self.cuarto.fuera_de_rango(self.ultima_temperatura, self.ultima_humedad):
            return ESTADO_FUERA_DE_RANGO
        if self.cuarto.en_advertencia(self.ultima_temperatura):
            return ESTADO_ADVERTENCIA
        return ESTADO_NORMAL

    class Meta:
        ordering = ['cuarto__codigo', 'codigo']
        constraints = [
            models.UniqueConstraint(fields=['cuarto', 'codigo'], name='sensor_unico_por_cuarto'),
        ]
        verbose_name_plural = "Sensores"


class Lectura(models.Model):
    # El historial: una fila por medición guardada. Es la tabla que más crece.
    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name='lecturas')
    fecha_hora = models.DateTimeField(db_index=True)
    temperatura = models.FloatField(help_text="°C")
    humedad = models.FloatField(null=True, blank=True, help_text="% (vacío si el sensor no mide humedad)")

    def __str__(self):
        return f"{self.sensor} {self.fecha_hora:%Y-%m-%d %H:%M} {self.temperatura} °C"

    class Meta:
        # Índice para las consultas típicas: "lecturas del sensor X entre tal y tal fecha"
        indexes = [models.Index(fields=['sensor', 'fecha_hora'], name='lectura_sensor_fecha')]
        verbose_name_plural = "Lecturas"


def _duracion_seg(inicio, fin):
    return int(((fin or timezone.now()) - inicio).total_seconds())


class Alerta(models.Model):
    TEMP_ALTA = TEMP_ALTA
    TEMP_BAJA = TEMP_BAJA
    HUM_ALTA = HUM_ALTA
    HUM_BAJA = HUM_BAJA
    SIN_DATOS = SIN_DATOS
    TIPOS = TIPOS_FUERA_DE_RANGO + [(SIN_DATOS, 'Sensor sin datos')]

    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name='alertas')
    tipo = models.CharField(max_length=20, choices=TIPOS)
    inicio = models.DateTimeField(help_text="Cuándo empezó el problema.")
    fin = models.DateTimeField(null=True, blank=True, help_text="Cuándo se normalizó (vacío = sigue activa).")
    valor_limite = models.FloatField(null=True, blank=True, help_text="Límite que se rebasó.")
    valor_extremo = models.FloatField(null=True, blank=True, help_text="Peor valor registrado durante la alerta.")
    mensaje = models.TextField()

    # Seguimiento del operador
    atendida = models.BooleanField(default=False)
    atendida_en = models.DateTimeField(null=True, blank=True)
    atendida_por = models.CharField(max_length=150, blank=True)
    nota = models.TextField(blank=True, help_text="Qué se hizo para resolverla.")

    def __str__(self):
        return f"{self.get_tipo_display()} en {self.sensor} ({self.inicio:%Y-%m-%d %H:%M})"

    @property
    def activa(self):
        return self.fin is None

    @property
    def duracion_seg(self):
        return _duracion_seg(self.inicio, self.fin)

    def es_peor(self, valor):
        return es_peor_valor(self.tipo, self.valor_extremo, valor)

    class Meta:
        ordering = ['-inicio']
        indexes = [models.Index(fields=['sensor', 'tipo', 'fin'], name='alerta_abierta')]
        verbose_name_plural = "Alertas"


class Excursion(models.Model):
    """
    Cada vez que un valor sale del rango permitido, aunque sea por segundos (ej. abrir la puerta).
    Si la excursión dura más de `minutos_para_alerta`, además se crea una Alerta y se enlaza aquí.
    """
    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name='excursiones')
    tipo = models.CharField(max_length=20, choices=TIPOS_FUERA_DE_RANGO)
    inicio = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True, help_text="Vacío = sigue fuera de rango.")
    valor_limite = models.FloatField(help_text="Límite que se rebasó.")
    valor_extremo = models.FloatField(help_text="Peor valor registrado durante la excursión.")
    alerta = models.OneToOneField(
        Alerta, null=True, blank=True, on_delete=models.SET_NULL, related_name='excursion',
        help_text="Alerta generada porque la excursión duró demasiado (si la hubo).",
    )

    def __str__(self):
        return f"{self.get_tipo_display()} en {self.sensor} ({self.inicio:%Y-%m-%d %H:%M})"

    @property
    def activa(self):
        return self.fin is None

    @property
    def duracion_seg(self):
        return _duracion_seg(self.inicio, self.fin)

    @property
    def es_larga(self):
        return self.alerta_id is not None

    def es_peor(self, valor):
        return es_peor_valor(self.tipo, self.valor_extremo, valor)

    class Meta:
        ordering = ['-inicio']
        indexes = [models.Index(fields=['sensor', 'tipo', 'fin'], name='excursion_abierta')]
        verbose_name = "Excursión"
        verbose_name_plural = "Excursiones"
