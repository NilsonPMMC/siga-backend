from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import OficioViewSet, GerarTextoOficioIAView
from atendimentos.views import GerarPdfOficioView

# Cria um roteador para registrar os ViewSets
router = DefaultRouter()

# Registra o OficioViewSet com a rota 'oficios'
# O basename é importante para a nomeação das URLs geradas
router.register(r'oficios', OficioViewSet, basename='oficio')

# As URLs da aplicação são definidas aqui
urlpatterns = [
    # Inclui todas as URLs geradas automaticamente pelo roteador
    path('oficios/<int:pk>/pdf/', GerarPdfOficioView.as_view(), name='oficio-pdf'),
    path('oficios/gerar-texto-ia/', GerarTextoOficioIAView.as_view(), name='oficio-gerar-texto-ia'),
    path('', include(router.urls)),
]