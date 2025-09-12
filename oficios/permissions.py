from rest_framework import permissions

class CanManageOficiosPermission(permissions.BasePermission):
    """
    Permissão personalizada para verificar se o usuário pode gerenciar o módulo de Ofícios.
    """
    def has_permission(self, request, view):
        # Permite acesso se o usuário for superusuário OU tiver a permissão específica.
        return request.user.is_superuser or request.user.has_perm('oficios.pode_gerenciar_oficios')