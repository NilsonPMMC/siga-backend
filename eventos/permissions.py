from rest_framework.permissions import BasePermission

class PodeGerenciarEventos(BasePermission):
    """
    Permissão customizada que verifica se o usuário:
    1. É um superusuário.
    2. Ou pertence ao grupo 'Gestor de Eventos'.
    """
    def has_permission(self, request, view):
        if request.user.is_superuser:
            return True

        # Verifica se o usuário tem a permissão específica, 
        # seja diretamente ou através de um grupo.
        return request.user.has_perm('eventos.pode_gerenciar_eventos')