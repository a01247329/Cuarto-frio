from django.test import SimpleTestCase

from monitoreo.protocolo import ErrorProtocolo, Medicion, construir_linea, interpretar_linea
from monitoreo.simulador import SimuladorSTM32


class ProtocoloTests(SimpleTestCase):

    def test_ejemplo_del_contrato(self):
        # Mismo ejemplo que aparece en CONTRATOS.md
        self.assertEqual(
            interpretar_linea('$TH,C01,S01,435,872*03'),
            Medicion(cuarto='C01', sensor='S01', temperatura=4.35, humedad=87.2),
        )

    def test_ida_y_vuelta(self):
        linea = construir_linea('C01', 'S01', 4.35, 87.2)
        self.assertEqual(linea, '$TH,C01,S01,435,872*03\n')
        self.assertEqual(interpretar_linea(linea).temperatura, 4.35)

    def test_temperatura_negativa_y_sin_humedad(self):
        m = interpretar_linea('$TH,C01,S02,-1850,*2E')
        self.assertEqual(m.temperatura, -18.5)
        self.assertIsNone(m.humedad)

    def test_acepta_bytes_con_retorno_de_carro(self):
        m = interpretar_linea(b'$TH,C01,S01,435,872*03\r\n')
        self.assertEqual(m.humedad, 87.2)

    def test_checksum_en_minusculas(self):
        linea = construir_linea('C01', 'S01', 3.0, 85.0).strip()
        cuerpo, cs = linea.rsplit('*', 1)
        self.assertEqual(interpretar_linea(f'{cuerpo}*{cs.lower()}').temperatura, 3.0)

    def test_lineas_invalidas(self):
        casos = {
            'TH,C01,S01,435,872*03': "no empieza con '$'",
            '$TH,C01,S01,435,872': "Falta el '*'",
            '$TH,C01,S01,436,872*03': 'Checksum incorrecto',
        }
        for linea, motivo in casos.items():
            with self.subTest(linea=linea), self.assertRaisesMessage(ErrorProtocolo, motivo):
                interpretar_linea(linea)

    def test_contenido_invalido_con_checksum_correcto(self):
        # Aunque el checksum cuadre, el contenido se valida
        casos = [
            ('XX,C01,S01,435,872', 'Tipo de mensaje desconocido'),
            ('TH,C01,S01,435', 'Se esperaban 5 campos'),
            ('TH,c01,S01,435,872', 'Código de cuarto inválido'),
            ('TH,C01,S01,4.35,872', 'no es un número entero'),
            ('TH,C01,S01,9000,872', 'Temperatura imposible'),
            ('TH,C01,S01,435,1200', 'Humedad imposible'),
        ]
        from monitoreo.protocolo import calcular_checksum
        for cuerpo, motivo in casos:
            with self.subTest(cuerpo=cuerpo), self.assertRaisesMessage(ErrorProtocolo, motivo):
                interpretar_linea(f'${cuerpo}*{calcular_checksum(cuerpo)}')

    def test_simulador_produce_lineas_del_protocolo(self):
        simulador = SimuladorSTM32(sensores=8, semilla=1)
        validas = invalidas = 0
        temperaturas = []
        sensores = set()
        for _ in range(4 * 720):  # 4 horas simuladas
            for linea in simulador.avanzar(5.0):
                try:
                    m = interpretar_linea(linea)
                    temperaturas.append(m.temperatura)
                    sensores.add(m.sensor)
                    validas += 1
                except ErrorProtocolo:
                    invalidas += 1
        self.assertEqual(sensores, {f'S{i:02d}' for i in range(1, 9)})
        self.assertGreater(validas, 0.95 * 8 * 4 * 720)
        self.assertGreater(invalidas, 0)             # algunos mensajes dañados a propósito
        self.assertLess(min(temperaturas), 10.0)     # pasa tiempo en reposo (~8.5 °C, azul)
        self.assertGreater(max(temperaturas), 13.0)  # y hay eventos de alarma (rojo)

    def test_simulador_a_veces_un_sensor_no_manda(self):
        simulador = SimuladorSTM32(sensores=8, semilla=2)
        rondas = [len(simulador.avanzar(5.0)) for _ in range(6 * 720)]
        self.assertLess(min(rondas), 8)  # en algún momento faltó al menos un sensor ("Offline")

    def test_simulador_entrega_una_linea_a_la_vez(self):
        simulador = SimuladorSTM32(sensores=3, cada_seg=60, semilla=3)
        primeras = [simulador.leer_linea() for _ in range(3)]
        self.assertTrue(all(linea and linea.startswith('$TH,C01,S0') for linea in primeras))
        self.assertIsNone(simulador.leer_linea(espera_max=0.01))  # la siguiente ronda aún no "llega"
