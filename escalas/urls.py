from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import relatorio_escala_pdf, EscalaPeriodoViewSet, EscalaRegistroViewSet, ContatoEmergenciaViewSet, ServidorLookupView

router = DefaultRouter()
router.register(r'periodos', EscalaPeriodoViewSet, basename='escala-periodos')
router.register(r'registros', EscalaRegistroViewSet, basename='escala-registros')
router.register(r'emergencia', ContatoEmergenciaViewSet, basename='contatos-emergencia')

urlpatterns = [
    path('', include(router.urls)),
    path('servidores/lookup/', ServidorLookupView.as_view(), name='servidores-lookup'),
    path('relatorio/<int:periodo_id>/', relatorio_escala_pdf, name='relatorio_escala_pdf'),
]