# etiquetas/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EtiquetaTemplateViewSet, GerarEtiquetaAPIView

# O router cuida de gerar automaticamente as URLs para o ViewSet (list, detail, etc.)
router = DefaultRouter()
router.register(r'templates', EtiquetaTemplateViewSet, basename='etiqueta-template')

urlpatterns = [
    # URLs geradas pelo router (ex: /api/etiquetas/templates/)
    path('', include(router.urls)),
    
    # URL específica para a nossa ação de gerar as etiquetas
    path('gerar/', GerarEtiquetaAPIView.as_view(), name='gerar-etiqueta'),
]