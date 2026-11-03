"""
Estado en memoria para transmitir datos en vivo a la interfaz (SSE).

Mismo patrón que serial_reader.py de la báscula: el lector actualiza este estado
y despierta a los que esperan en esperar_cambio(); /api/stream/ lo envía al navegador.
"""
import copy
import threading

_condicion = threading.Condition()
_version = 0
_estado = {
    'lector': {
        'modo': None,
        'conectado': False,
        'puerto': None,
        'error': None,
        'mensajes_validos': 0,
        'mensajes_invalidos': 0,
        'ultimo_error_protocolo': None,
    },
    # "C01/S01" -> {cuarto, sensor, temperatura, humedad, fecha_hora, estado}
    'sensores': {},
    # Aumenta cada vez que se crea, cierra o atiende una alerta
    'alertas_version': 0,
}


def _notificar():
    global _version
    _version += 1
    _condicion.notify_all()


def actualizar_lector(**campos):
    with _condicion:
        _estado['lector'].update(campos)
        _notificar()


def incrementar_lector(campo):
    with _condicion:
        _estado['lector'][campo] += 1
        _notificar()


def actualizar_sensor(clave, datos):
    with _condicion:
        _estado['sensores'][clave] = datos
        _notificar()


def alertas_cambiaron():
    with _condicion:
        _estado['alertas_version'] += 1
        _notificar()


def obtener():
    with _condicion:
        return _version, copy.deepcopy(_estado)


def obtener_lector():
    with _condicion:
        return dict(_estado['lector'])


def esperar_cambio(version, timeout=15.0):
    with _condicion:
        _condicion.wait_for(lambda: _version != version, timeout=timeout)
