# eventos/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

# Cria um router e registra nossa viewset com ele.
router = DefaultRouter()
router.register(r'eventos', views.EventoViewSet, basename='evento')
router.register(r'convidados', views.ConvidadoViewSet, basename='convidado')
router.register(r'comunicacoes', views.ComunicacaoViewSet, basename='comunicacao')
router.register(r'destinatarios', views.DestinatarioViewSet, basename='destinatario')
router.register(r'logs-de-envio', views.LogDeEnvioViewSet, basename='logdeenvio')
router.register(r'lista-presenca', views.ListaPresencaViewSet, basename='listapresenca')
router.register(r'checklists', views.EventoChecklistViewSet, basename='eventochecklist')

public_urls = [
    path('public/checklist/<uuid:token>/', views.preencher_checklist, name='preencher_checklist'),
    path('public/presenca/<int:evento_id>/', views.registrar_presenca, name='registrar_presenca'),
    path('public/presenca/sucesso/', views.presenca_sucesso, name='presenca_sucesso'),
    path('public/check-in/<int:conta_id>/', views.PublicCheckInView.as_view(), name='public-check-in'),
    path('public/checklist/<uuid:token>/', views.PublicChecklistView.as_view(), name='public-checklist-view'),
]

# As URLs da API são determinadas automaticamente pelo router.
urlpatterns = [
    path('', include(router.urls)),
    path('', include(public_urls)),
]