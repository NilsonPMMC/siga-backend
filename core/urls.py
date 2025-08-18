# /core/urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from atendimentos.views import MyTokenObtainPairView
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path('admin/', admin.site.urls),

    path('api/token/', MyTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Esta é a única linha necessária para direcionar para nosso app
    path('api/', include('eventos.urls')),
    path('api/', include('atendimentos.urls')),
   
    # Esta linha é necessária para o fluxo de redefinição de senha do Django
    path('accounts/', include('django.contrib.auth.urls')),
]

# Configuração para servir arquivos de mídia em ambiente de desenvolvimento
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)