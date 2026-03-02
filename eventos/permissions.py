from rest_framework.permissions import BasePermission

from atendimentos.permissions import is_in_group


class PodeGerenciarEventos(BasePermission):
    """
    Permissão customizada que verifica se o usuário:
    1. É um superusuário.
    2. Ou pertence ao grupo 'Gestor de Eventos'.
    3. Ou tem a permissão 'eventos.pode_gerenciar_eventos' (via grupo ou atribuição direta).
    """
    def has_permission(self, request, view):
        if request.user.is_superuser:
            return True

        # Verifica se pertence ao grupo Gestor de Eventos (independente da permissão no Admin)
        if is_in_group(request.user, 'Gestor de Eventos'):
            return True

        # Verifica se tem a permissão específica no Django (grupo com permissão atribuída)
        return request.user.has_perm('eventos.pode_gerenciar_eventos')