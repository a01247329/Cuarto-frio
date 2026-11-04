"""
Panel /admin/: sirve para ver y editar las tablas mientras no exista la interfaz.
"""
from django import forms
from django.contrib import admin
from django.db import models
from django.utils import timezone

from . import en_vivo
from .models import Alerta, Configuracion, CuartoFrio, Excursion, Lectura, Sensor


class DecimalConComa(forms.FloatField):
    """Acepta 8.5 y también 8,5 (como se escribe en México)."""
    widget = forms.TextInput(attrs={'inputmode': 'decimal', 'size': 8})

    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip().replace(',', '.')
        return super().to_python(value)


# Se usa campo de texto en lugar del campo numérico del navegador, porque algunos
# navegadores bloquean "8,5" sin avisar y el formulario no se envía.
DECIMALES_CON_COMA = {models.FloatField: {'form_class': DecimalConComa}}


@admin.register(Configuracion)
class ConfiguracionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'modo', 'puerto_serial', 'baudrate', 'intervalo_guardado_seg', 'dias_retencion', 'minutos_sin_datos')

    # Es una sola fila: no se puede agregar otra ni borrarla
    def has_add_permission(self, request):
        return not Configuracion.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class SensorEnLinea(admin.TabularInline):
    model = Sensor
    extra = 0
    fields = ('codigo', 'descripcion', 'activo', 'ultima_lectura', 'ultima_temperatura', 'ultima_humedad')
    readonly_fields = ('ultima_lectura', 'ultima_temperatura', 'ultima_humedad')


@admin.register(CuartoFrio)
class CuartoFrioAdmin(admin.ModelAdmin):
    formfield_overrides = DECIMALES_CON_COMA
    list_display = ('codigo', 'nombre', 'temp_min', 'temp_advertencia', 'temp_max', 'hum_min', 'hum_max', 'minutos_para_alerta', 'activo')
    inlines = [SensorEnLinea]


@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'descripcion', 'activo', 'ultima_lectura', 'ultima_temperatura', 'ultima_humedad')
    list_filter = ('cuarto', 'activo')
    readonly_fields = ('ultima_lectura', 'ultima_temperatura', 'ultima_humedad')


@admin.register(Lectura)
class LecturaAdmin(admin.ModelAdmin):
    list_display = ('fecha_hora', 'sensor', 'temperatura', 'humedad')
    list_filter = ('sensor__cuarto', 'sensor')
    date_hierarchy = 'fecha_hora'
    ordering = ('-fecha_hora',)
    list_select_related = ('sensor__cuarto',)

    # El historial no se edita a mano
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Excursion)
class ExcursionAdmin(admin.ModelAdmin):
    list_display = ('inicio', 'tipo', 'sensor', 'valor_extremo', 'fin', 'duracion_seg', 'es_larga')
    list_filter = ('tipo', 'sensor__cuarto')
    date_hierarchy = 'inicio'
    ordering = ('-inicio',)
    list_select_related = ('sensor__cuarto',)

    # Las genera el procesador; no se editan a mano
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Alerta)
class AlertaAdmin(admin.ModelAdmin):
    list_display = ('inicio', 'tipo', 'sensor', 'valor_extremo', 'fin', 'atendida')
    list_filter = ('tipo', 'atendida', 'sensor__cuarto')
    date_hierarchy = 'inicio'
    readonly_fields = ('sensor', 'tipo', 'inicio', 'fin', 'valor_limite', 'valor_extremo', 'mensaje', 'atendida_en', 'atendida_por')
    fields = readonly_fields[:7] + ('atendida', 'atendida_en', 'atendida_por', 'nota')
    list_select_related = ('sensor__cuarto',)

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        # Al marcarla como atendida desde el admin se registra quién y cuándo
        if obj.atendida and obj.atendida_en is None:
            obj.atendida_en = timezone.now()
            obj.atendida_por = request.user.get_username()
        super().save_model(request, obj, form, change)
        en_vivo.alertas_cambiaron()
