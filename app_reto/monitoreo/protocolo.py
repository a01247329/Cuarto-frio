"""
Protocolo serial STM32 -> PC (el "contrato 1", ver CONTRATOS.md).

Cada medición es una línea de texto:

    $TH,<cuarto>,<sensor>,<temp_x100>,<hum_x10>*<checksum>\n

    $TH,C01,S01,435,872*03    -> cuarto C01, sensor S01, 4.35 °C, 87.2 %
    $TH,C01,S02,-1850,*2E     -> temperatura -18.50 °C, sensor sin humedad

- Los valores van como enteros (x100 y x10) porque en el STM32 el printf con
  decimales viene desactivado por defecto.
- El checksum es el XOR de todos los bytes entre '$' y '*', en 2 dígitos
  hexadecimales. Sirve para descartar mensajes dañados por ruido en el cable.
- La fecha y hora las pone la PC al recibir el mensaje, no el STM32.
"""
import re
from dataclasses import dataclass

TIPO_MENSAJE = 'TH'

# Rangos físicamente posibles: fuera de esto es un error del sensor, no un dato real
TEMP_MIN_VALIDA = -40.0
TEMP_MAX_VALIDA = 85.0

_CODIGO_VALIDO = re.compile(r'^[A-Z0-9]{1,10}$')


class ErrorProtocolo(ValueError):
    """La línea recibida no cumple el formato acordado."""


@dataclass(frozen=True)
class Medicion:
    cuarto: str
    sensor: str
    temperatura: float
    humedad: float | None


def calcular_checksum(cuerpo):
    resultado = 0
    for byte in cuerpo.encode('ascii'):
        resultado ^= byte
    return f'{resultado:02X}'


def construir_linea(cuarto, sensor, temperatura, humedad=None):
    # Lo usa el simulador; el STM32 debe producir exactamente lo mismo
    humedad_txt = '' if humedad is None else str(round(humedad * 10))
    cuerpo = f'{TIPO_MENSAJE},{cuarto},{sensor},{round(temperatura * 100)},{humedad_txt}'
    return f'${cuerpo}*{calcular_checksum(cuerpo)}\n'


def interpretar_linea(linea):
    """Convierte una línea recibida en una Medicion, o lanza ErrorProtocolo explicando por qué no."""
    if isinstance(linea, (bytes, bytearray)):
        try:
            linea = linea.decode('ascii')
        except UnicodeDecodeError:
            raise ErrorProtocolo('La línea contiene caracteres que no son ASCII.')
    linea = linea.strip()

    if not linea.isascii():
        raise ErrorProtocolo('La línea contiene caracteres que no son ASCII.')
    if not linea.startswith('$'):
        raise ErrorProtocolo("La línea no empieza con '$'.")
    if '*' not in linea:
        raise ErrorProtocolo("Falta el '*' que separa el checksum.")

    cuerpo, _, checksum_recibido = linea[1:].rpartition('*')
    checksum_calculado = calcular_checksum(cuerpo)
    if checksum_recibido.upper() != checksum_calculado:
        raise ErrorProtocolo(f'Checksum incorrecto: llegó {checksum_recibido!r}, se esperaba {checksum_calculado!r}.')

    partes = cuerpo.split(',')
    if len(partes) != 5:
        raise ErrorProtocolo(f'Se esperaban 5 campos separados por coma y llegaron {len(partes)}.')
    tipo, cuarto, sensor, temp_txt, hum_txt = partes

    if tipo != TIPO_MENSAJE:
        raise ErrorProtocolo(f'Tipo de mensaje desconocido: {tipo!r}.')
    for nombre, codigo in (('cuarto', cuarto), ('sensor', sensor)):
        if not _CODIGO_VALIDO.match(codigo):
            raise ErrorProtocolo(f'Código de {nombre} inválido: {codigo!r} (solo mayúsculas y números, máx. 10).')

    try:
        temperatura = int(temp_txt) / 100
    except ValueError:
        raise ErrorProtocolo(f'La temperatura no es un número entero: {temp_txt!r}.')
    if not (TEMP_MIN_VALIDA <= temperatura <= TEMP_MAX_VALIDA):
        raise ErrorProtocolo(f'Temperatura imposible: {temperatura} °C.')

    humedad = None
    if hum_txt != '':
        try:
            humedad = int(hum_txt) / 10
        except ValueError:
            raise ErrorProtocolo(f'La humedad no es un número entero: {hum_txt!r}.')
        if not (0.0 <= humedad <= 100.0):
            raise ErrorProtocolo(f'Humedad imposible: {humedad} %.')

    return Medicion(cuarto=cuarto, sensor=sensor, temperatura=temperatura, humedad=humedad)
