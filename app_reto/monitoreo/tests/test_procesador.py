from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from monitoreo.models import Alerta, CuartoFrio, Excursion, Lectura, Sensor
from monitoreo.procesador import CuartoNoRegistrado, Procesador
from monitoreo.protocolo import Medicion

# Las migraciones crean el cuarto C01 con los sensores S01..S08 y la configuración por defecto
# (guardar cada 60 s, 5 min sin datos, 60 días de retención). Los tests fijan sus propios
# límites para no depender de los valores de la demo.


def medicion(temperatura, humedad=88.0, cuarto='C01', sensor='S01'):
    return Medicion(cuarto=cuarto, sensor=sensor, temperatura=temperatura, humedad=humedad)


def fijar_limites_de_prueba(**cambios):
    limites = dict(temp_min=2.0, temp_advertencia=None, temp_max=8.0, hum_min=80.0, hum_max=95.0, minutos_para_alerta=10)
    limites.update(cambios)
    CuartoFrio.objects.filter(codigo='C01').update(**limites)


class GuardadoTests(TestCase):

    def setUp(self):
        self.t0 = timezone.now()
        self.procesador = Procesador(inicio=self.t0)

    def test_guarda_solo_una_lectura_por_intervalo(self):
        self.assertTrue(self.procesador.procesar(medicion(4.0), self.t0).guardada)
        self.assertFalse(self.procesador.procesar(medicion(4.1), self.t0 + timedelta(seconds=10)).guardada)
        self.assertTrue(self.procesador.procesar(medicion(4.2), self.t0 + timedelta(seconds=60)).guardada)
        self.assertEqual(Lectura.objects.count(), 2)

    def test_actualiza_ultimo_valor_en_cada_mensaje(self):
        self.procesador.procesar(medicion(4.0), self.t0)
        self.procesador.procesar(medicion(4.7, 90.0), self.t0 + timedelta(seconds=5))
        sensor = Sensor.objects.get(codigo='S01')
        self.assertEqual(sensor.ultima_temperatura, 4.7)
        self.assertEqual(sensor.ultima_humedad, 90.0)

    def test_registra_sensor_nuevo_automaticamente(self):
        self.procesador.procesar(medicion(4.0, sensor='S07'), self.t0)
        self.assertTrue(Sensor.objects.filter(cuarto__codigo='C01', codigo='S07').exists())

    def test_cuarto_desconocido_se_rechaza(self):
        with self.assertRaises(CuartoNoRegistrado):
            self.procesador.procesar(medicion(4.0, cuarto='C99'), self.t0)
        self.assertEqual(Lectura.objects.count(), 0)

    def test_sensor_inactivo_se_ignora(self):
        Sensor.objects.filter(codigo='S01').update(activo=False)
        self.procesador.procesar(medicion(4.0), self.t0)
        self.assertEqual(Lectura.objects.count(), 0)

    def test_sensor_sin_humedad(self):
        self.procesador.procesar(medicion(-18.0, humedad=None), self.t0)
        self.assertIsNone(Lectura.objects.get().humedad)


class AlertasTests(TestCase):

    def setUp(self):
        fijar_limites_de_prueba()
        self.t0 = timezone.now()
        self.procesador = Procesador(inicio=self.t0)

    def en(self, minutos, temperatura, humedad=88.0):
        return self.procesador.procesar(medicion(temperatura, humedad), self.t0 + timedelta(minutes=minutos))

    def test_alerta_solo_despues_del_tiempo_configurado(self):
        self.assertEqual(self.en(0, 9.0).alertas_nuevas, [])
        self.assertEqual(self.en(5, 9.5).alertas_nuevas, [])
        nuevas = self.en(10, 9.2).alertas_nuevas
        self.assertEqual(len(nuevas), 1)
        alerta = nuevas[0]
        self.assertEqual(alerta.tipo, Alerta.TEMP_ALTA)
        self.assertEqual(alerta.inicio, self.t0)  # empezó cuando salió de rango
        self.assertEqual(alerta.valor_limite, 8.0)
        self.assertIn('temperatura alta', alerta.mensaje)

    def test_no_duplica_y_guarda_el_peor_valor(self):
        self.en(0, 9.0)
        self.en(10, 9.0)
        self.assertEqual(self.en(11, 11.5).alertas_nuevas, [])
        self.en(12, 10.0)
        alerta = Alerta.objects.get()
        self.assertEqual(alerta.valor_extremo, 11.5)

    def test_se_cierra_al_regresar_al_rango(self):
        self.en(0, 9.0)
        self.en(10, 9.0)
        cerradas = self.en(15, 5.0).alertas_cerradas
        self.assertEqual(len(cerradas), 1)
        alerta = Alerta.objects.get()
        self.assertFalse(alerta.activa)
        self.assertEqual(alerta.fin, self.t0 + timedelta(minutes=15))

    def test_abrir_la_puerta_no_genera_alerta(self):
        # Sale de rango 5 min, regresa, vuelve a salir: el conteo empieza de nuevo
        self.en(0, 9.0)
        self.en(5, 9.0)
        self.en(6, 5.0)
        self.en(7, 9.0)
        self.assertEqual(self.en(16, 9.0).alertas_nuevas, [])  # solo 9 min seguidos
        self.assertEqual(len(self.en(17, 9.0).alertas_nuevas), 1)

    def test_alerta_de_temperatura_baja_y_humedad(self):
        self.en(0, 1.0, humedad=70.0)
        nuevas = self.en(10, 1.0, humedad=70.0).alertas_nuevas
        self.assertEqual({a.tipo for a in nuevas}, {Alerta.TEMP_BAJA, Alerta.HUM_BAJA})

    def test_limites_por_cuarto(self):
        CuartoFrio.objects.filter(codigo='C01').update(temp_max=10.0, minutos_para_alerta=0)
        self.assertEqual(self.en(0, 9.0).alertas_nuevas, [])
        self.assertEqual(len(self.en(1, 10.5).alertas_nuevas), 1)  # 0 min = alerta inmediata

    def test_estado_del_sensor(self):
        fijar_limites_de_prueba(temp_advertencia=6.0)
        sensor = Sensor.objects.get(codigo='S01')
        self.assertEqual(sensor.calcular_estado(), 'sin_datos')
        for minuto, temperatura, estado in [(0, 4.0, 'normal'), (1, 6.5, 'advertencia'),
                                            (2, 9.0, 'fuera_de_rango'), (12, 9.0, 'alerta')]:
            self.en(minuto, temperatura)
            sensor.refresh_from_db()
            self.assertEqual(sensor.calcular_estado(), estado, f'minuto {minuto}, {temperatura} °C')

    def test_advertencia_debe_estar_entre_minimo_y_maximo(self):
        from django.core.exceptions import ValidationError
        cuarto = CuartoFrio.objects.get(codigo='C01')
        cuarto.temp_advertencia = 9.0  # mayor que el máximo (8)
        with self.assertRaises(ValidationError):
            cuarto.full_clean()


class ExcursionesTests(TestCase):
    """Cada salida de rango, aunque sea corta, queda registrada (lo pide la lista de alarmas de la interfaz)."""

    def setUp(self):
        fijar_limites_de_prueba()
        self.t0 = timezone.now()
        self.procesador = Procesador(inicio=self.t0)

    def en(self, segundos, temperatura, humedad=88.0):
        return self.procesador.procesar(medicion(temperatura, humedad), self.t0 + timedelta(seconds=segundos))

    def test_salida_corta_queda_como_excursion_sin_alerta(self):
        self.en(0, 5.0)
        self.assertEqual(len(self.en(10, 9.0).excursiones_nuevas), 1)
        self.en(20, 10.5)
        self.en(40, 9.5)
        self.en(50, 5.0)  # regresa al rango
        excursion = Excursion.objects.get()
        self.assertEqual(excursion.tipo, Alerta.TEMP_ALTA)
        self.assertEqual(excursion.valor_extremo, 10.5)
        self.assertEqual(excursion.valor_limite, 8.0)
        self.assertEqual(excursion.duracion_seg, 40)
        self.assertFalse(excursion.es_larga)
        self.assertFalse(Alerta.objects.exists())

    def test_excursion_larga_queda_enlazada_a_su_alerta(self):
        self.en(0, 9.0)
        alerta = self.en(600, 9.0).alertas_nuevas[0]
        excursion = Excursion.objects.get()
        self.assertTrue(excursion.es_larga)
        self.assertEqual(excursion.alerta, alerta)
        self.assertEqual(alerta.inicio, excursion.inicio)

    def test_cada_salida_es_una_excursion_distinta(self):
        for segundos, temperatura in [(0, 9.0), (10, 5.0), (20, 9.0), (30, 5.0), (40, 1.0), (50, 5.0)]:
            self.en(segundos, temperatura)
        self.assertEqual(Excursion.objects.count(), 3)
        self.assertEqual(Excursion.objects.filter(fin__isnull=True).count(), 0)

    def test_humedad_genera_excursion_propia(self):
        self.en(0, 5.0, humedad=70.0)
        self.assertEqual(Excursion.objects.get().tipo, Alerta.HUM_BAJA)

    def test_al_arrancar_se_cierran_las_que_quedaron_abiertas(self):
        self.en(0, 9.0)
        self.en(600, 9.0)  # excursión + alerta abiertas
        cerradas = Procesador().cerrar_pendientes_al_arrancar()
        self.assertEqual(cerradas, 2)
        self.assertFalse(Excursion.objects.filter(fin__isnull=True).exists())
        self.assertEqual(Alerta.objects.get().fin, self.t0 + timedelta(seconds=600))  # en el último dato conocido


class SinDatosTests(TestCase):

    def setUp(self):
        # Solo S01 activo, para contar sus alertas
        Sensor.objects.exclude(codigo='S01').update(activo=False)
        self.t0 = timezone.now()
        self.procesador = Procesador(inicio=self.t0)

    def test_alerta_sin_datos_y_se_cierra_al_llegar_un_dato(self):
        self.assertEqual(self.procesador.revisar_sin_datos(self.t0 + timedelta(minutes=4)), [])
        nuevas = self.procesador.revisar_sin_datos(self.t0 + timedelta(minutes=5))
        self.assertEqual(len(nuevas), 1)
        self.assertEqual(nuevas[0].tipo, Alerta.SIN_DATOS)
        # No se duplica
        self.assertEqual(self.procesador.revisar_sin_datos(self.t0 + timedelta(minutes=6)), [])

        resultado = self.procesador.procesar(medicion(4.0), self.t0 + timedelta(minutes=7))
        self.assertEqual([a.tipo for a in resultado.alertas_cerradas], [Alerta.SIN_DATOS])

    def test_cuenta_desde_el_ultimo_dato(self):
        self.procesador.procesar(medicion(4.0), self.t0 + timedelta(minutes=3))
        self.assertEqual(self.procesador.revisar_sin_datos(self.t0 + timedelta(minutes=7)), [])
        self.assertEqual(len(self.procesador.revisar_sin_datos(self.t0 + timedelta(minutes=8))), 1)

    def test_no_alerta_por_el_tiempo_que_la_pc_estuvo_apagada(self):
        # El último dato es de hace 3 días, pero el programa acaba de arrancar
        Sensor.objects.filter(codigo='S01').update(ultima_lectura=self.t0 - timedelta(days=3))
        self.assertEqual(self.procesador.revisar_sin_datos(self.t0 + timedelta(minutes=1)), [])


class LimpiezaTests(TestCase):

    def test_borra_solo_lo_viejo(self):
        ahora = timezone.now()
        sensor = Sensor.objects.get(codigo='S01')
        Lectura.objects.create(sensor=sensor, fecha_hora=ahora - timedelta(days=61), temperatura=4.0)
        Lectura.objects.create(sensor=sensor, fecha_hora=ahora - timedelta(days=59), temperatura=4.0)
        Alerta.objects.create(sensor=sensor, tipo=Alerta.TEMP_ALTA, inicio=ahora - timedelta(days=70),
                              fin=ahora - timedelta(days=65), mensaje='vieja cerrada')
        Alerta.objects.create(sensor=sensor, tipo=Alerta.TEMP_BAJA, inicio=ahora - timedelta(days=70),
                              mensaje='vieja pero sigue activa')
        Excursion.objects.create(sensor=sensor, tipo=Alerta.TEMP_ALTA, inicio=ahora - timedelta(days=62),
                                 fin=ahora - timedelta(days=62), valor_limite=8, valor_extremo=9)
        Excursion.objects.create(sensor=sensor, tipo=Alerta.TEMP_ALTA, inicio=ahora - timedelta(days=1),
                                 fin=ahora - timedelta(days=1), valor_limite=8, valor_extremo=9)

        borrados = Procesador().limpiar(ahora)

        self.assertEqual(borrados, {'lecturas': 1, 'excursiones': 1, 'alertas': 1})
        self.assertEqual(Lectura.objects.count(), 1)
        self.assertEqual(Excursion.objects.count(), 1)
        self.assertEqual(Alerta.objects.get().mensaje, 'vieja pero sigue activa')
