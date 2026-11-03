"""
API para la interfaz (el "contrato 2", ver CONTRATOS.md).

Todas las respuestas son JSON. Todas requieren sesión iniciada (401 si no),
excepto iniciar sesión y registrarse.
Los errores siempre tienen la forma {"error": "mensaje"}.
Las fechas se entregan en ISO 8601 con hora local, ej. "2026-10-14T10:05:00-06:00".
"""
import json
from datetime import datetime, time as hora, timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Avg, Count, Max, Min
from django.db.models.functions import TruncHour
from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import en_vivo
from .models import Alerta, Configuracion, CuartoFrio, Excursion, Lectura, peor_estado, unidad_de

LIMITE_LECTURAS_DEFECTO = 2000
LIMITE_LECTURAS_MAXIMO = 20000
LIMITE_ALERTAS_DEFECTO = 200
LIMITE_EXCURSIONES_DEFECTO = 100


class ErrorPeticion(Exception):
    """La petición trae datos inválidos; se responde 400 con el mensaje."""


def _error(mensaje, status=400):
    return JsonResponse({'error': mensaje}, status=status)


def api_view(*metodos, publica=False):
    """Decorador común: métodos permitidos, sesión obligatoria (salvo `publica`) y errores en JSON."""
    def decorador(vista):
        @csrf_exempt
        @require_http_methods(list(metodos))
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not publica and not request.user.is_authenticated:
                return _error('Debes iniciar sesión.', 401)
            try:
                return vista(request, *args, **kwargs)
            except ErrorPeticion as e:
                return _error(str(e), 400)
        return envoltura
    return decorador


# ---------------------------------------------------------------------------
# Utilidades para leer parámetros
# ---------------------------------------------------------------------------

def _leer_json(request):
    try:
        datos = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        raise ErrorPeticion('El cuerpo de la petición no es JSON válido.')
    if not isinstance(datos, dict):
        raise ErrorPeticion('El cuerpo debe ser un objeto JSON, ej. {"campo": valor}.')
    return datos


def _fecha(texto, nombre):
    # Acepta "2026-10-14T10:00:00", con o sin zona horaria, o solo "2026-10-14"
    valor = parse_datetime(texto)
    if valor is None:
        dia = parse_date(texto)
        if dia is None:
            raise ErrorPeticion(f'"{nombre}" no es una fecha válida (usa 2026-10-14 o 2026-10-14T10:00:00).')
        valor = datetime.combine(dia, hora.min)
    if timezone.is_naive(valor):
        valor = timezone.make_aware(valor)
    return valor


def _rango_fechas(request, horas_defecto=24):
    hasta = _fecha(request.GET['hasta'], 'hasta') if request.GET.get('hasta') else timezone.now()
    desde = _fecha(request.GET['desde'], 'desde') if request.GET.get('desde') else hasta - timedelta(hours=horas_defecto)
    if desde > hasta:
        raise ErrorPeticion('"desde" debe ser anterior a "hasta".')
    return desde, hasta


def _entero(texto, nombre, defecto, minimo, maximo):
    if texto in (None, ''):
        return defecto
    try:
        valor = int(texto)
    except ValueError:
        raise ErrorPeticion(f'"{nombre}" debe ser un número entero.')
    return max(minimo, min(maximo, valor))


def _booleano(texto, nombre):
    if texto in (None, ''):
        return None
    if texto.lower() in ('true', '1', 'si', 'sí'):
        return True
    if texto.lower() in ('false', '0', 'no'):
        return False
    raise ErrorPeticion(f'"{nombre}" debe ser true o false.')


def _aplicar_cambios(objeto, datos, campos):
    """Copia los campos permitidos de `datos` al objeto, valida y guarda."""
    desconocidos = set(datos) - set(campos)
    if desconocidos:
        raise ErrorPeticion(f'Campos no permitidos: {", ".join(sorted(desconocidos))}. Permitidos: {", ".join(campos)}.')
    for nombre, valor in datos.items():
        tipo = campos[nombre]
        if valor is None and objeto._meta.get_field(nombre).null:
            setattr(objeto, nombre, None)
            continue
        if tipo is bool and not isinstance(valor, bool):
            raise ErrorPeticion(f'"{nombre}" debe ser true o false.')
        try:
            setattr(objeto, nombre, tipo(valor))
        except (TypeError, ValueError):
            raise ErrorPeticion(f'"{nombre}" tiene un valor inválido: {valor!r}.')
    try:
        objeto.full_clean()
    except ValidationError as e:
        mensajes = [f'{campo}: {" ".join(errores)}' for campo, errores in e.message_dict.items()]
        raise ErrorPeticion(' | '.join(mensajes))
    objeto.save()


# ---------------------------------------------------------------------------
# Conversión de objetos a JSON
# ---------------------------------------------------------------------------

def _iso(fecha):
    return timezone.localtime(fecha).isoformat() if fecha else None


def _sensor_json(sensor, ahora):
    return {
        'codigo': sensor.codigo,
        'nombre': sensor.nombre,
        'descripcion': sensor.descripcion,
        'activo': sensor.activo,
        'en_linea': sensor.en_linea(ahora),
        'estado': sensor.calcular_estado(),
        'temperatura': sensor.ultima_temperatura,
        'humedad': sensor.ultima_humedad,
        'fecha_hora': _iso(sensor.ultima_lectura),
    }


def _cuarto_json(cuarto):
    ahora = timezone.now()
    sensores = [_sensor_json(s, ahora) for s in cuarto.sensores.all()]
    # Promedio solo de los sensores activos que están mandando datos
    vigentes = [s['temperatura'] for s in sensores if s['activo'] and s['en_linea'] and s['temperatura'] is not None]
    return {
        'codigo': cuarto.codigo,
        'nombre': cuarto.nombre,
        'activo': cuarto.activo,
        'estado': peor_estado(s['estado'] for s in sensores if s['activo']),
        'temperatura_promedio': round(sum(vigentes) / len(vigentes), 2) if vigentes else None,
        'limites': {
            'temp_min': cuarto.temp_min,
            'temp_advertencia': cuarto.temp_advertencia,
            'temp_max': cuarto.temp_max,
            'hum_min': cuarto.hum_min,
            'hum_max': cuarto.hum_max,
        },
        'minutos_para_alerta': cuarto.minutos_para_alerta,
        'sensores': sensores,
    }


def _alerta_json(alerta):
    return {
        'id': alerta.id,
        'tipo': alerta.tipo,
        'tipo_texto': alerta.get_tipo_display(),
        'cuarto': alerta.sensor.cuarto.codigo,
        'sensor': alerta.sensor.codigo,
        'sensor_nombre': alerta.sensor.nombre,
        'mensaje': alerta.mensaje,
        'inicio': _iso(alerta.inicio),
        'fin': _iso(alerta.fin),
        'activa': alerta.activa,
        'duracion_seg': alerta.duracion_seg,
        'valor_limite': alerta.valor_limite,
        'valor_extremo': alerta.valor_extremo,
        'atendida': alerta.atendida,
        'atendida_en': _iso(alerta.atendida_en),
        'atendida_por': alerta.atendida_por,
        'nota': alerta.nota,
    }


def _excursion_json(excursion):
    return {
        'id': excursion.id,
        'tipo': excursion.tipo,
        'tipo_texto': excursion.get_tipo_display(),
        'cuarto': excursion.sensor.cuarto.codigo,
        'sensor': excursion.sensor.codigo,
        'sensor_nombre': excursion.sensor.nombre,
        'inicio': _iso(excursion.inicio),
        'fin': _iso(excursion.fin),
        'activa': excursion.activa,
        'duracion_seg': excursion.duracion_seg,
        'valor_limite': excursion.valor_limite,
        'valor_extremo': excursion.valor_extremo,
        'unidad': unidad_de(excursion.tipo),
        'es_larga': excursion.es_larga,
        'alerta_id': excursion.alerta_id,
    }


def _config_json(config):
    return {
        'modo': config.modo,
        'puerto_serial': config.puerto_serial,
        'baudrate': config.baudrate,
        'intervalo_guardado_seg': config.intervalo_guardado_seg,
        'dias_retencion': config.dias_retencion,
        'minutos_sin_datos': config.minutos_sin_datos,
        'sensores_simulados': config.sensores_simulados,
    }


def _usuario_json(usuario):
    return {'usuario': usuario.get_username(), 'es_admin': usuario.is_staff}


# ---------------------------------------------------------------------------
# Sesión y registro
# ---------------------------------------------------------------------------

@api_view('GET', 'POST', 'DELETE', publica=True)
def sesion(request):
    """GET: ¿quién soy?  POST: iniciar sesión.  DELETE: cerrar sesión."""
    if request.method == 'GET':
        if not request.user.is_authenticated:
            return _error('No hay sesión iniciada.', 401)
        return JsonResponse(_usuario_json(request.user))

    if request.method == 'DELETE':
        logout(request)
        return JsonResponse({'ok': True})

    datos = _leer_json(request)
    usuario = authenticate(request, username=str(datos.get('usuario', '')).strip(),
                           password=str(datos.get('contrasena', '')))
    if usuario is None:
        return _error('Usuario o contraseña incorrectos.', 400)
    login(request, usuario)
    return JsonResponse(_usuario_json(usuario))


@api_view('POST', publica=True)
def registro(request):
    """Crea una cuenta de operador (sin permisos de administrador)."""
    if not getattr(settings, 'PERMITIR_REGISTRO', False):
        return _error('El registro de cuentas está desactivado. Pide una cuenta al administrador.', 403)

    datos = _leer_json(request)
    nombre = str(datos.get('usuario', '')).strip()
    contrasena = str(datos.get('contrasena', ''))
    if not nombre or not contrasena:
        raise ErrorPeticion('Escribe un usuario y una contraseña.')

    usuario = get_user_model()(username=nombre)
    try:
        # Valida caracteres permitidos, longitud y que no exista otro igual
        usuario.full_clean(exclude=['password'])
        # Reglas de seguridad de la contraseña (las mismas de settings.AUTH_PASSWORD_VALIDATORS)
        validate_password(contrasena, usuario)
    except ValidationError as e:
        raise ErrorPeticion(' '.join(e.messages))
    usuario.set_password(contrasena)
    try:
        usuario.save()
    except IntegrityError:
        raise ErrorPeticion('Ya existe un usuario con ese nombre.')
    return JsonResponse(_usuario_json(usuario), status=201)


# ---------------------------------------------------------------------------
# Endpoints de datos
# ---------------------------------------------------------------------------

@api_view('GET')
def estado_sistema(request):
    return JsonResponse({
        'hora_servidor': _iso(timezone.now()),
        'lector': en_vivo.obtener_lector(),
        'configuracion': _config_json(Configuracion.load()),
    })


@api_view('GET')
def cuartos(request):
    lista = CuartoFrio.objects.prefetch_related('sensores')
    return JsonResponse({'cuartos': [_cuarto_json(c) for c in lista]})


@api_view('GET', 'POST')
def cuarto_detalle(request, codigo):
    cuarto = CuartoFrio.objects.filter(codigo=codigo).first()
    if cuarto is None:
        return _error(f'No existe el cuarto {codigo!r}.', 404)
    if request.method == 'POST':
        _aplicar_cambios(cuarto, _leer_json(request), {
            'nombre': str, 'temp_min': float, 'temp_advertencia': float, 'temp_max': float,
            'hum_min': float, 'hum_max': float, 'minutos_para_alerta': int, 'activo': bool,
        })
    return JsonResponse(_cuarto_json(cuarto))


@api_view('GET')
def lecturas(request):
    desde, hasta = _rango_fechas(request)
    limite = _entero(request.GET.get('limite'), 'limite', LIMITE_LECTURAS_DEFECTO, 1, LIMITE_LECTURAS_MAXIMO)

    consulta = Lectura.objects.filter(fecha_hora__gte=desde, fecha_hora__lte=hasta).select_related('sensor__cuarto')
    if request.GET.get('cuarto'):
        consulta = consulta.filter(sensor__cuarto__codigo=request.GET['cuarto'])
    if request.GET.get('sensor'):
        consulta = consulta.filter(sensor__codigo=request.GET['sensor'])

    # Se toman las más recientes y luego se ordenan de la más vieja a la más nueva (lo que pide una gráfica)
    filas = list(consulta.order_by('-fecha_hora')[:limite + 1])
    truncado = len(filas) > limite
    filas = filas[:limite]
    filas.reverse()

    return JsonResponse({
        'desde': _iso(desde),
        'hasta': _iso(hasta),
        'total': len(filas),
        'truncado': truncado,
        'lecturas': [{
            'cuarto': f.sensor.cuarto.codigo,
            'sensor': f.sensor.codigo,
            'fecha_hora': _iso(f.fecha_hora),
            'temperatura': f.temperatura,
            'humedad': f.humedad,
        } for f in filas],
    })


def _redondear(valor):
    return round(valor, 2) if valor is not None else None


def _contar_por_sensor(modelo, desde, hasta, cuarto):
    consulta = modelo.objects.filter(inicio__gte=desde, inicio__lte=hasta)
    if cuarto:
        consulta = consulta.filter(sensor__cuarto__codigo=cuarto)
    return dict(consulta.values_list('sensor_id').annotate(n=Count('id')))


@api_view('GET')
def lecturas_resumen(request):
    desde, hasta = _rango_fechas(request)
    cuarto = request.GET.get('cuarto')
    consulta = Lectura.objects.filter(fecha_hora__gte=desde, fecha_hora__lte=hasta)
    if cuarto:
        consulta = consulta.filter(sensor__cuarto__codigo=cuarto)

    grupos = (consulta
              .values('sensor_id', 'sensor__cuarto__codigo', 'sensor__codigo', 'sensor__descripcion')
              .annotate(total=Count('id'),
                        temp_min=Min('temperatura'), temp_max=Max('temperatura'), temp_promedio=Avg('temperatura'),
                        hum_min=Min('humedad'), hum_max=Max('humedad'), hum_promedio=Avg('humedad'))
              .order_by('sensor__cuarto__codigo', 'sensor__codigo'))
    excursiones = _contar_por_sensor(Excursion, desde, hasta, cuarto)
    alertas = _contar_por_sensor(Alerta, desde, hasta, cuarto)

    return JsonResponse({
        'desde': _iso(desde),
        'hasta': _iso(hasta),
        'sensores': [{
            'cuarto': g['sensor__cuarto__codigo'],
            'sensor': g['sensor__codigo'],
            'nombre': g['sensor__descripcion'] or g['sensor__codigo'],
            'total_lecturas': g['total'],
            'temperatura': {'min': g['temp_min'], 'max': g['temp_max'], 'promedio': _redondear(g['temp_promedio'])},
            'humedad': {'min': g['hum_min'], 'max': g['hum_max'], 'promedio': _redondear(g['hum_promedio'])},
            'excursiones': excursiones.get(g['sensor_id'], 0),
            'alertas': alertas.get(g['sensor_id'], 0),
        } for g in grupos],
    })


@api_view('GET')
def lecturas_por_hora(request):
    """Promedios por hora: del cuarto completo (por defecto) o de cada sensor (por_sensor=true)."""
    desde, hasta = _rango_fechas(request, horas_defecto=24 * 7)
    por_sensor = _booleano(request.GET.get('por_sensor'), 'por_sensor') or False
    consulta = Lectura.objects.filter(fecha_hora__gte=desde, fecha_hora__lte=hasta)
    if request.GET.get('cuarto'):
        consulta = consulta.filter(sensor__cuarto__codigo=request.GET['cuarto'])
    if request.GET.get('sensor'):
        consulta = consulta.filter(sensor__codigo=request.GET['sensor'])

    campos = ['hora'] + (['sensor__codigo'] if por_sensor else [])
    grupos = (consulta
              .annotate(hora=TruncHour('fecha_hora', tzinfo=timezone.get_current_timezone()))
              .values(*campos)
              .annotate(total=Count('id'),
                        temp_promedio=Avg('temperatura'), temp_min=Min('temperatura'), temp_max=Max('temperatura'),
                        hum_promedio=Avg('humedad'))
              .order_by(*campos))

    puntos = []
    for g in grupos:
        punto = {
            'hora': _iso(g['hora']),
            'total_lecturas': g['total'],
            'temp_promedio': _redondear(g['temp_promedio']),
            'temp_min': g['temp_min'],
            'temp_max': g['temp_max'],
            'hum_promedio': _redondear(g['hum_promedio']),
        }
        if por_sensor:
            punto['sensor'] = g['sensor__codigo']
        puntos.append(punto)

    return JsonResponse({
        'desde': _iso(desde),
        'hasta': _iso(hasta),
        'agrupado_por': 'sensor' if por_sensor else 'cuarto',
        'puntos': puntos,
    })


@api_view('GET')
def excursiones(request):
    desde, hasta = _rango_fechas(request, horas_defecto=24 * 7)
    consulta = Excursion.objects.select_related('sensor__cuarto').filter(inicio__gte=desde, inicio__lte=hasta)
    activa = _booleano(request.GET.get('activa'), 'activa')
    if activa is not None:
        consulta = consulta.filter(fin__isnull=activa)
    larga = _booleano(request.GET.get('larga'), 'larga')
    if larga is not None:
        consulta = consulta.filter(alerta__isnull=not larga)
    if request.GET.get('cuarto'):
        consulta = consulta.filter(sensor__cuarto__codigo=request.GET['cuarto'])
    if request.GET.get('sensor'):
        consulta = consulta.filter(sensor__codigo=request.GET['sensor'])
    if request.GET.get('tipo'):
        consulta = consulta.filter(tipo=request.GET['tipo'])
    limite = _entero(request.GET.get('limite'), 'limite', LIMITE_EXCURSIONES_DEFECTO, 1, 1000)

    lista = list(consulta.order_by('-inicio')[:limite])
    return JsonResponse({'total': len(lista), 'excursiones': [_excursion_json(e) for e in lista]})


@api_view('GET')
def alertas(request):
    consulta = Alerta.objects.select_related('sensor__cuarto')
    activa = _booleano(request.GET.get('activa'), 'activa')
    if activa is not None:
        consulta = consulta.filter(fin__isnull=activa)
    atendida = _booleano(request.GET.get('atendida'), 'atendida')
    if atendida is not None:
        consulta = consulta.filter(atendida=atendida)
    if request.GET.get('cuarto'):
        consulta = consulta.filter(sensor__cuarto__codigo=request.GET['cuarto'])
    if request.GET.get('tipo'):
        consulta = consulta.filter(tipo=request.GET['tipo'])
    limite = _entero(request.GET.get('limite'), 'limite', LIMITE_ALERTAS_DEFECTO, 1, 1000)

    lista = list(consulta.order_by('-inicio')[:limite])
    return JsonResponse({'total': len(lista), 'alertas': [_alerta_json(a) for a in lista]})


@api_view('POST')
def alerta_atender(request, alerta_id):
    alerta = Alerta.objects.select_related('sensor__cuarto').filter(pk=alerta_id).first()
    if alerta is None:
        return _error(f'No existe la alerta {alerta_id}.', 404)
    datos = _leer_json(request)
    alerta.atendida = True
    alerta.atendida_en = timezone.now()
    alerta.atendida_por = request.user.get_username()
    alerta.nota = str(datos.get('nota', alerta.nota))
    alerta.save(update_fields=['atendida', 'atendida_en', 'atendida_por', 'nota'])
    en_vivo.alertas_cambiaron()
    return JsonResponse(_alerta_json(alerta))


@api_view('GET', 'POST')
def configuracion(request):
    config = Configuracion.load()
    if request.method == 'POST':
        _aplicar_cambios(config, _leer_json(request), {
            'modo': str, 'puerto_serial': str, 'baudrate': int,
            'intervalo_guardado_seg': int, 'dias_retencion': int, 'minutos_sin_datos': int,
            'sensores_simulados': int,
        })
    return JsonResponse(_config_json(config))


@api_view('GET')
def stream(request):
    # Server-Sent Events: manda el estado en vivo cada vez que cambia
    def eventos():
        version = -1
        while True:
            nueva_version, datos = en_vivo.obtener()
            if nueva_version != version:
                version = nueva_version
                yield f"data: {json.dumps(datos)}\n\n"
            else:
                yield ": keep-alive\n\n"
            en_vivo.esperar_cambio(version, timeout=15.0)

    respuesta = StreamingHttpResponse(eventos(), content_type='text/event-stream')
    respuesta['Cache-Control'] = 'no-cache'
    respuesta['X-Accel-Buffering'] = 'no'
    return respuesta
