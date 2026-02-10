# /var/www/gabinete/siga-gabinete/oficios/views.py

from rest_framework import viewsets, permissions
from django.shortcuts import get_object_or_404
# Imports de IA removidos (google.generativeai, settings, etc)

from .models import Oficio
from .serializers import OficioSerializer
from .permissions import CanManageOficiosPermission

class OficioViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gerenciar Ofícios.
    Oferece funcionalidades completas de CRUD (Create, Retrieve, Update, Destroy).
    """
    serializer_class = OficioSerializer
    permission_classes = [CanManageOficiosPermission] 

    def get_queryset(self):
        """
        Sobrescreve o método queryset para filtrar os ofícios
        com base na(s) conta(s) do usuário logado.
        Superusuários podem ver todos os ofícios.
        """
        user = self.request.user

        # Superusuários têm acesso a todos os ofícios de todas as contas.
        if user.is_superuser:
            return Oficio.objects.all()

        # Usuários com perfil associado veem apenas os ofícios
        # das contas às quais estão vinculados.
        if hasattr(user, 'perfil'):
            return Oficio.objects.filter(conta__in=user.perfil.contas.all())

        # Se o usuário não for superusuário e não tiver um perfil com contas,
        # ele não poderá ver nenhum ofício.
        return Oficio.objects.none()

    def perform_create(self, serializer):
        """
        Sobrescreve o método de criação para associar automaticamente
        o ofício ao usuário que o está criando.
        """
        serializer.save(criado_por=self.request.user)