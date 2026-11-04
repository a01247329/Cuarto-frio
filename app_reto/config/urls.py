from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    # Todo lo que usa la interfaz vive bajo /api/ (ver CONTRATOS.md)
    path('api/', include('monitoreo.urls')),
    path('', RedirectView.as_view(url='/admin/', permanent=False)),
]
