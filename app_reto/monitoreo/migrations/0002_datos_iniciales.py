# Datos iniciales: la configuración y el cuarto frío que se va a simular (C01 con sensor S01)
from django.db import migrations


def crear_datos_iniciales(apps, schema_editor):
    Configuracion = apps.get_model('monitoreo', 'Configuracion')
    CuartoFrio = apps.get_model('monitoreo', 'CuartoFrio')
    Sensor = apps.get_model('monitoreo', 'Sensor')

    Configuracion.objects.get_or_create(pk=1)
    cuarto, _ = CuartoFrio.objects.get_or_create(codigo='C01', defaults={'nombre': 'Cuarto frío 1'})
    Sensor.objects.get_or_create(cuarto=cuarto, codigo='S01', defaults={'descripcion': 'Sensor principal'})


class Migration(migrations.Migration):

    dependencies = [
        ('monitoreo', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(crear_datos_iniciales, migrations.RunPython.noop),
    ]
