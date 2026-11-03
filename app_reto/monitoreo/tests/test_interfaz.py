"""
Pruebas de la interfaz contra un servidor real: se levanta Django en un puerto temporal
y se usa el mismo ApiClient y Actualizador que usa la ventana.
"""
import importlib.util
import queue
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import LiveServerTestCase
from django.utils import timezone

from monitoreo.models import Excursion, Lectura, Sensor

_ruta = Path(settings.BASE_DIR) / 'interfaz' / 'monitor_cuartos_frios.py'
_spec = importlib.util.spec_from_file_location('monitor_cuartos_frios', _ruta)
interfaz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(interfaz)


class ClienteDeLaInterfazTests(LiveServerTestCase):

    def setUp(self):
        User.objects.create_user('operador', password='clave-de-prueba')
        self.cliente = interfaz.ApiClient(self.live_server_url)

    def test_sesion_expirada_sin_iniciar_sesion(self):
        with self.assertRaises(interfaz.SesionExpirada):
            self.cliente.get('/api/cuartos/C01/')

    def test_contrasena_incorrecta_da_mensaje_del_servidor(self):
        with self.assertRaisesMessage(interfaz.ErrorApi, 'Usuario o contraseña incorrectos.'):
            self.cliente.iniciar_sesion('operador', 'otra')

    def test_servidor_apagado_es_error_de_conexion(self):
        apagado = interfaz.ApiClient('http://127.0.0.1:9')
        with self.assertRaises(interfaz.ErrorConexion):
            apagado.iniciar_sesion('operador', 'clave-de-prueba')

    def test_registro_e_inicio_de_sesion(self):
        with self.assertRaises(interfaz.ErrorApi):
            self.cliente.registrar('nuevo', '123')  # contraseña débil
        self.cliente.registrar('nuevo', 'Camara-fria-2026')
        self.assertEqual(self.cliente.iniciar_sesion('nuevo', 'Camara-fria-2026')['usuario'], 'nuevo')

    def test_actualizador_entrega_todo_lo_que_pinta_la_ventana(self):
        ahora = timezone.now()
        s01 = Sensor.objects.get(codigo='S01')
        Sensor.objects.filter(pk=s01.pk).update(ultima_lectura=ahora, ultima_temperatura=11.2, ultima_humedad=88.0)
        for horas in (3, 2, 1):
            Lectura.objects.create(sensor=s01, fecha_hora=ahora - timedelta(hours=horas), temperatura=8.0 + horas)
        Excursion.objects.create(sensor=s01, tipo='temp_alta', inicio=ahora - timedelta(minutes=3),
                                 fin=ahora - timedelta(minutes=2), valor_limite=13, valor_extremo=13.6)

        self.cliente.iniciar_sesion('operador', 'clave-de-prueba')
        cola = queue.Queue()
        actualizador = interfaz.Actualizador(self.cliente, cola, generacion=1, horas_estadisticas=24)
        actualizador.start()
        recibido = {}
        try:
            while not {'cuarto', 'alarmas', 'historial', 'estadisticas', 'conectado'} <= set(recibido):
                generacion, tipo, datos = cola.get(timeout=10)
                self.assertNotEqual(tipo, 'error', datos)
                recibido[tipo] = datos
        finally:
            actualizador.detener()

        sensor = recibido['cuarto']['sensores'][0]
        self.assertEqual((sensor['nombre'], sensor['estado'], sensor['en_linea']), ('Sensor 1', 'advertencia', True))
        self.assertEqual(len(recibido['historial']['puntos']), 3)
        alarma = recibido['alarmas'][0]
        self.assertEqual((alarma['texto'], alarma['duracion_seg'], alarma['larga']), ('Sensor 1 13.6°C', 60, False))
        self.assertEqual(recibido['estadisticas']['sensores'][0]['excursiones'], 1)

    def test_formatos(self):
        self.assertEqual(interfaz.formato_duracion(6), '06s')
        self.assertEqual(interfaz.formato_duracion(600), '10 min')
        self.assertEqual(interfaz.formato_duracion(3900), '1 h 5 min')
        self.assertEqual(interfaz.formato_valor(13.56, '°C'), '13.6°C')
        self.assertEqual(interfaz.formato_valor(79.6, '%'), '79.6%')
