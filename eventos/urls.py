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
router.register(r'checklist-items', views.EventoChecklistItemStatusViewSet, basename='eventochecklistitem')
router.register(r'master-checklist-items', views.ChecklistItemViewSet, basename='masterchecklistitem')
router.register(r'mailing-lists', views.MailingListViewSet, basename='mailinglist')
router.register(r'email-supressoes', views.EmailSupressaoViewSet, basename='emailsupressao')

public_urls = [
    path('public/presenca/<int:evento_id>/', views.registrar_presenca, name='registrar_presenca'),
    path('public/presenca/sucesso/', views.presenca_sucesso, name='presenca_sucesso'),
    path('public/check-in/<int:conta_id>/', views.PublicCheckInView.as_view(), name='public-check-in'),
    path('public/checklist/<uuid:token>/', views.PublicChecklistView.as_view(), name='public-checklist-view'),
    path('mailing-list/<int:pk>/export/csv/', views.ExportMailingListCSVView.as_view(), name='export-mailing-list-csv'),
]

# As URLs da API são determinadas automaticamente pelo router.
urlpatterns = [
    path('eventos/bi/analytics/', views.EventoAnalyticsView.as_view(), name='evento-bi-analytics'),
    path('eventos/bi/analytics/pdf/', views.GerarPdfBiEventosView.as_view(), name='evento-bi-pdf'),
    path('', include(router.urls)),
    path('', include(public_urls)),
]