from django.urls import path
from . import api

urlpatterns = [
    path('sesion/', api.sesion, name='api_sesion'),
    path('registro/', api.registro, name='api_registro'),
    path('estado/', api.estado_sistema, name='api_estado'),
    path('cuartos/', api.cuartos, name='api_cuartos'),
    path('cuartos/<str:codigo>/', api.cuarto_detalle, name='api_cuarto_detalle'),
    path('lecturas/', api.lecturas, name='api_lecturas'),
    path('lecturas/resumen/', api.lecturas_resumen, name='api_lecturas_resumen'),
    path('lecturas/por_hora/', api.lecturas_por_hora, name='api_lecturas_por_hora'),
    path('excursiones/', api.excursiones, name='api_excursiones'),
    path('alertas/', api.alertas, name='api_alertas'),
    path('alertas/<int:alerta_id>/atender/', api.alerta_atender, name='api_alerta_atender'),
    path('configuracion/', api.configuracion, name='api_configuracion'),
    path('stream/', api.stream, name='api_stream'),
]
