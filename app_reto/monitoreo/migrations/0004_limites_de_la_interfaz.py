# Ajusta el cuarto simulado a los valores de la interfaz del equipo:
# advertencia (amarillo) desde 10 °C y alarma (rojo) desde 13 °C, con 8 sensores.
from django.db import migrations

SENSORES = 8


def aplicar(apps, schema_editor):
    CuartoFrio = apps.get_model('monitoreo', 'CuartoFrio')
    Sensor = apps.get_model('monitoreo', 'Sensor')

    cuarto = CuartoFrio.objects.filter(codigo='C01').first()
    if cuarto is None:
        return
    cuarto.temp_min = 2.0
    cuarto.temp_advertencia = 10.0
    cuarto.temp_max = 13.0
    cuarto.save(update_fields=['temp_min', 'temp_advertencia', 'temp_max'])

    for i in range(1, SENSORES + 1):
        sensor, _ = Sensor.objects.get_or_create(cuarto=cuarto, codigo=f'S{i:02d}')
        # El nombre que muestra la interfaz ("Sensor 1"…)
        if not sensor.descripcion or sensor.descripcion == 'Sensor principal':
            sensor.descripcion = f'Sensor {i}'
            sensor.save(update_fields=['descripcion'])


def revertir(apps, schema_editor):
    CuartoFrio = apps.get_model('monitoreo', 'CuartoFrio')
    CuartoFrio.objects.filter(codigo='C01').update(temp_min=2.0, temp_advertencia=None, temp_max=8.0)


class Migration(migrations.Migration):

    dependencies = [
        ('monitoreo', '0003_excursiones_advertencia_y_sensores_simulados'),
    ]

    operations = [
        migrations.RunPython(aplicar, revertir),
    ]
