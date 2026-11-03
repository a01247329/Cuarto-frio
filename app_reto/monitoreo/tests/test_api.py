import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from monitoreo.models import Alerta, Configuracion, CuartoFrio, Excursion, Lectura, Sensor


class ApiTests(TestCase):

    def setUp(self):
        self.usuario = User.objects.create_user('operador', password='clave-de-prueba')
        self.client.force_login(self.usuario)
        self.sensor = Sensor.objects.get(codigo='S01')
        self.ahora = timezone.now()

    def post_json(self, url, datos):
        return self.client.post(url, data=json.dumps(datos), content_type='application/json')

    def test_requiere_sesion(self):
        self.client.logout()
        respuesta = self.client.get('/api/cuartos/')
        self.assertEqual(respuesta.status_code, 401)
        self.assertIn('error', respuesta.json())

    def test_cuartos(self):
        Sensor.objects.filter(pk=self.sensor.pk).update(
            ultima_lectura=self.ahora, ultima_temperatura=8.3, ultima_humedad=88.0)
        datos = self.client.get('/api/cuartos/').json()
        cuarto = datos['cuartos'][0]
        self.assertEqual(cuarto['codigo'], 'C01')
        # Valores de la demo (migración 0004): advertencia 10 °C, alarma 13 °C
        self.assertEqual(cuarto['limites']['temp_advertencia'], 10.0)
        self.assertEqual(cuarto['limites']['temp_max'], 13.0)
        self.assertEqual(len(cuarto['sensores']), 8)
        s01 = cuarto['sensores'][0]
        self.assertEqual((s01['codigo'], s01['nombre'], s01['temperatura']), ('S01', 'Sensor 1', 8.3))
        self.assertEqual(s01['estado'], 'normal')
        self.assertTrue(s01['en_linea'])
        self.assertFalse(cuarto['sensores'][1]['en_linea'])  # S02 nunca ha mandado datos
        # El promedio solo usa sensores en línea
        self.assertEqual(cuarto['temperatura_promedio'], 8.3)

    def test_sensor_fuera_de_linea_tras_30_segundos(self):
        Sensor.objects.filter(pk=self.sensor.pk).update(
            ultima_lectura=self.ahora - timedelta(seconds=31), ultima_temperatura=8.0)
        cuarto = self.client.get('/api/cuartos/C01/').json()
        self.assertFalse(cuarto['sensores'][0]['en_linea'])
        self.assertIsNone(cuarto['temperatura_promedio'])

    def test_estado_advertencia(self):
        Sensor.objects.filter(pk=self.sensor.pk).update(ultima_lectura=self.ahora, ultima_temperatura=11.0)
        self.assertEqual(self.client.get('/api/cuartos/C01/').json()['sensores'][0]['estado'], 'advertencia')

    def test_editar_advertencia(self):
        self.assertEqual(self.post_json('/api/cuartos/C01/', {'temp_advertencia': 11.5}).status_code, 200)
        self.assertEqual(CuartoFrio.objects.get(codigo='C01').temp_advertencia, 11.5)
        self.assertEqual(self.post_json('/api/cuartos/C01/', {'temp_advertencia': None}).status_code, 200)
        self.assertIsNone(CuartoFrio.objects.get(codigo='C01').temp_advertencia)
        self.assertEqual(self.post_json('/api/cuartos/C01/', {'temp_advertencia': 20}).status_code, 400)

    def test_editar_limites_del_cuarto(self):
        respuesta = self.post_json('/api/cuartos/C01/', {'temp_min': 0, 'temp_advertencia': 3, 'temp_max': 4, 'minutos_para_alerta': 15})
        self.assertEqual(respuesta.status_code, 200)
        cuarto = CuartoFrio.objects.get(codigo='C01')
        self.assertEqual((cuarto.temp_min, cuarto.temp_advertencia, cuarto.temp_max, cuarto.minutos_para_alerta), (0, 3, 4, 15))
        # Bajar el máximo por debajo de la advertencia vigente se rechaza
        self.assertEqual(self.post_json('/api/cuartos/C01/', {'temp_max': 2.5}).status_code, 400)

    def test_editar_limites_invalidos(self):
        self.assertEqual(self.post_json('/api/cuartos/C01/', {'temp_min': 9, 'temp_max': 4}).status_code, 400)
        self.assertEqual(self.post_json('/api/cuartos/C01/', {'temperatura_max': 4}).status_code, 400)
        self.assertEqual(self.post_json('/api/cuartos/C01/', {'activo': 'si'}).status_code, 400)
        self.assertEqual(self.client.get('/api/cuartos/C99/').status_code, 404)

    def test_lecturas_por_rango_en_orden_cronologico(self):
        for minutos in (90, 30, 10):
            Lectura.objects.create(sensor=self.sensor, fecha_hora=self.ahora - timedelta(minutes=minutos), temperatura=minutos / 10)
        desde = (self.ahora - timedelta(hours=1)).isoformat()
        datos = self.client.get('/api/lecturas/', {'cuarto': 'C01', 'desde': desde}).json()
        self.assertEqual(datos['total'], 2)
        self.assertEqual([l['temperatura'] for l in datos['lecturas']], [3.0, 1.0])
        self.assertFalse(datos['truncado'])

    def test_lecturas_con_limite(self):
        for minutos in range(5):
            Lectura.objects.create(sensor=self.sensor, fecha_hora=self.ahora - timedelta(minutes=minutos), temperatura=4.0)
        datos = self.client.get('/api/lecturas/', {'limite': 3}).json()
        self.assertEqual(datos['total'], 3)
        self.assertTrue(datos['truncado'])

    def test_lecturas_fecha_invalida(self):
        self.assertEqual(self.client.get('/api/lecturas/', {'desde': 'ayer'}).status_code, 400)

    def test_resumen(self):
        for temperatura in (3.0, 4.0, 5.0):
            Lectura.objects.create(sensor=self.sensor, fecha_hora=self.ahora - timedelta(minutes=5), temperatura=temperatura, humedad=90)
        Excursion.objects.create(sensor=self.sensor, tipo=Alerta.TEMP_ALTA, inicio=self.ahora - timedelta(minutes=4),
                                 fin=self.ahora, valor_limite=13, valor_extremo=14)
        resumen = self.client.get('/api/lecturas/resumen/').json()['sensores'][0]
        self.assertEqual(resumen['nombre'], 'Sensor 1')
        self.assertEqual(resumen['total_lecturas'], 3)
        self.assertEqual(resumen['temperatura'], {'min': 3.0, 'max': 5.0, 'promedio': 4.0})
        self.assertEqual((resumen['excursiones'], resumen['alertas']), (1, 0))

    def test_promedios_por_hora_del_cuarto(self):
        s02 = Sensor.objects.get(codigo='S02')
        base = timezone.localtime(self.ahora).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        # Hora 1: S01 = 8 y 10, S02 = 12  -> promedio del cuarto 10
        for sensor, minutos, temperatura in [(self.sensor, 5, 8.0), (self.sensor, 35, 10.0), (s02, 20, 12.0)]:
            Lectura.objects.create(sensor=sensor, fecha_hora=base + timedelta(minutes=minutos), temperatura=temperatura)
        # Hora 2: S01 = 9
        Lectura.objects.create(sensor=self.sensor, fecha_hora=base + timedelta(minutes=70), temperatura=9.0)

        datos = self.client.get('/api/lecturas/por_hora/', {'cuarto': 'C01'}).json()
        self.assertEqual(datos['agrupado_por'], 'cuarto')
        self.assertEqual([(p['temp_promedio'], p['total_lecturas']) for p in datos['puntos']], [(10.0, 3), (9.0, 1)])
        self.assertEqual(datos['puntos'][0]['hora'], base.isoformat())

        por_sensor = self.client.get('/api/lecturas/por_hora/', {'cuarto': 'C01', 'por_sensor': 'true'}).json()
        self.assertEqual([(p['sensor'], p['temp_promedio']) for p in por_sensor['puntos']],
                         [('S01', 9.0), ('S02', 12.0), ('S01', 9.0)])

    def test_excursiones(self):
        alerta = Alerta.objects.create(sensor=self.sensor, tipo=Alerta.TEMP_ALTA, inicio=self.ahora - timedelta(minutes=20),
                                       fin=self.ahora - timedelta(minutes=5), mensaje='larga')
        Excursion.objects.create(sensor=self.sensor, tipo=Alerta.TEMP_ALTA, inicio=self.ahora - timedelta(minutes=20),
                                 fin=self.ahora - timedelta(minutes=5), valor_limite=13, valor_extremo=15.2, alerta=alerta)
        Excursion.objects.create(sensor=self.sensor, tipo=Alerta.TEMP_ALTA, inicio=self.ahora - timedelta(minutes=2),
                                 fin=self.ahora - timedelta(minutes=2) + timedelta(seconds=30), valor_limite=13, valor_extremo=13.4)

        datos = self.client.get('/api/excursiones/', {'cuarto': 'C01'}).json()
        self.assertEqual(datos['total'], 2)
        reciente, larga = datos['excursiones']
        self.assertEqual((reciente['duracion_seg'], reciente['es_larga'], reciente['unidad']), (30, False, '°C'))
        self.assertEqual((larga['duracion_seg'], larga['es_larga'], larga['alerta_id']), (900, True, alerta.id))
        self.assertEqual(reciente['sensor_nombre'], 'Sensor 1')
        self.assertEqual(self.client.get('/api/excursiones/', {'larga': 'true'}).json()['total'], 1)

    def test_alertas_y_atender(self):
        alerta = Alerta.objects.create(sensor=self.sensor, tipo=Alerta.TEMP_ALTA, inicio=self.ahora - timedelta(minutes=12),
                                       fin=self.ahora, mensaje='prueba')
        datos = self.client.get('/api/alertas/', {'activa': 'false', 'atendida': 'false'}).json()
        self.assertEqual(datos['total'], 1)
        self.assertEqual(datos['alertas'][0]['tipo_texto'], 'Temperatura alta')
        self.assertEqual(datos['alertas'][0]['duracion_seg'], 720)

        respuesta = self.post_json(f'/api/alertas/{alerta.id}/atender/', {'nota': 'Se cerró la puerta'})
        self.assertEqual(respuesta.status_code, 200)
        alerta.refresh_from_db()
        self.assertTrue(alerta.atendida)
        self.assertEqual(alerta.atendida_por, 'operador')
        self.assertEqual(alerta.nota, 'Se cerró la puerta')
        self.assertEqual(self.client.get('/api/alertas/', {'atendida': 'false'}).json()['total'], 0)

    def test_configuracion(self):
        respuesta = self.post_json('/api/configuracion/', {'modo': 'serial', 'puerto_serial': 'COM5'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(Configuracion.load().puerto_serial, 'COM5')
        self.assertEqual(self.post_json('/api/configuracion/', {'modo': 'wifi'}).status_code, 400)
        self.assertEqual(self.post_json('/api/configuracion/', {'intervalo_guardado_seg': 0}).status_code, 400)

    def test_estado(self):
        datos = self.client.get('/api/estado/').json()
        self.assertIn('lector', datos)
        self.assertEqual(datos['configuracion']['dias_retencion'], 60)
        self.assertEqual(datos['configuracion']['sensores_simulados'], 8)


class AdminTests(TestCase):

    def test_limites_se_pueden_escribir_con_coma_o_punto(self):
        admin = User.objects.create_superuser('jefe', password='clave-de-prueba')
        self.client.force_login(admin)
        cuarto = CuartoFrio.objects.get(codigo='C01')
        url = f'/admin/monitoreo/cuartofrio/{cuarto.pk}/change/'
        base = {
            'codigo': 'C01', 'nombre': 'Cuarto frío 1', 'temp_min': '2', 'temp_max': '13',
            'hum_min': '80', 'hum_max': '95', 'minutos_para_alerta': '10', 'activo': 'on',
            'sensores-TOTAL_FORMS': '0', 'sensores-INITIAL_FORMS': '0',
            'sensores-MIN_NUM_FORMS': '0', 'sensores-MAX_NUM_FORMS': '1000',
        }
        for escrito, esperado in (('8,5', 8.5), ('9.5', 9.5)):
            with self.subTest(escrito=escrito):
                respuesta = self.client.post(url, {**base, 'temp_advertencia': escrito})
                self.assertEqual(respuesta.status_code, 302)  # 302 = guardado y redirigido a la lista
                cuarto.refresh_from_db()
                self.assertEqual(cuarto.temp_advertencia, esperado)
        # La validación de límites sigue funcionando
        respuesta = self.client.post(url, {**base, 'temp_advertencia': '20,0'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'La advertencia debe estar entre')


class SesionYRegistroTests(TestCase):

    def setUp(self):
        User.objects.create_user('operador', password='clave-de-prueba')

    def post_json(self, url, datos):
        return self.client.post(url, data=json.dumps(datos), content_type='application/json')

    def test_iniciar_y_cerrar_sesion(self):
        self.assertEqual(self.client.get('/api/sesion/').status_code, 401)
        respuesta = self.post_json('/api/sesion/', {'usuario': 'operador', 'contrasena': 'clave-de-prueba'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json(), {'usuario': 'operador', 'es_admin': False})
        self.assertEqual(self.client.get('/api/cuartos/').status_code, 200)

        self.assertEqual(self.client.delete('/api/sesion/').status_code, 200)
        self.assertEqual(self.client.get('/api/cuartos/').status_code, 401)

    def test_contrasena_incorrecta(self):
        respuesta = self.post_json('/api/sesion/', {'usuario': 'operador', 'contrasena': 'otra'})
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['error'], 'Usuario o contraseña incorrectos.')

    def test_registro_crea_operador_sin_permisos_de_admin(self):
        respuesta = self.post_json('/api/registro/', {'usuario': 'nuevo', 'contrasena': 'Camara-fria-2026'})
        self.assertEqual(respuesta.status_code, 201)
        usuario = User.objects.get(username='nuevo')
        self.assertFalse(usuario.is_staff or usuario.is_superuser)
        self.assertNotEqual(usuario.password, 'Camara-fria-2026')  # se guarda cifrada
        self.assertEqual(self.post_json('/api/sesion/', {'usuario': 'nuevo', 'contrasena': 'Camara-fria-2026'}).status_code, 200)

    def test_registro_rechaza_contrasenas_debiles_y_duplicados(self):
        for contrasena in ('abc', '12345678', 'password'):
            with self.subTest(contrasena=contrasena):
                self.assertEqual(self.post_json('/api/registro/', {'usuario': 'nuevo', 'contrasena': contrasena}).status_code, 400)
        respuesta = self.post_json('/api/registro/', {'usuario': 'operador', 'contrasena': 'Camara-fria-2026'})
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(self.post_json('/api/registro/', {'usuario': 'con espacio', 'contrasena': 'Camara-fria-2026'}).status_code, 400)
        self.assertFalse(User.objects.filter(username='nuevo').exists())

    def test_registro_se_puede_desactivar(self):
        with self.settings(PERMITIR_REGISTRO=False):
            respuesta = self.post_json('/api/registro/', {'usuario': 'nuevo', 'contrasena': 'Camara-fria-2026'})
        self.assertEqual(respuesta.status_code, 403)
