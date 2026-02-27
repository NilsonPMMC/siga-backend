from rest_framework.permissions import BasePermission, SAFE_METHODS

def is_in_group(user, group_names):
    """
    Função auxiliar para verificar se um usuário pertence a um ou mais grupos.
    Aceita uma string com um nome de grupo ou uma lista de strings.
    """
    if user and user.is_authenticated:
        if not isinstance(group_names, list):
            group_names = [group_names]
        return user.groups.filter(name__in=group_names).exists()
    return False

# --- NOSSAS LEIS FINAIS E REFINADAS ---

class CanManageAgendas(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return (
            user.is_superuser 
            or is_in_group(request.user, 'Secretária') 
            or is_in_group(request.user, 'Membro do Gabinete') # <<< LINHA ADICIONADA
        )
    
class CanAccessObjectByConta(BasePermission):
    """A Lei do Sigilo: Garante que um usuário só acesse objetos de sua Conta."""
    def has_object_permission(self, request, view, obj):
        user = request.user
        # Superusuário e Recepção podem acessar objetos de qualquer conta
        if user.is_superuser or is_in_group(user, 'Recepção'):
            return True

        if hasattr(user, 'perfil') and hasattr(obj, 'conta'):
            return obj.conta in user.perfil.contas.all()

        return False

class CanInteractWithAtendimento(BasePermission):
    """
    Regra ÚNICA e definitiva para TODAS as interações com um atendimento específico.
    """
    def has_object_permission(self, request, view, obj):
        user = request.user

        # 1. Superusuário pode tudo.
        if user.is_superuser:
            return True

        # 2. Regras para Recepção:
        #    Permite a interação (ver, editar, excluir) SOMENTE se o status for 'ABERTO'.
        if is_in_group(user, 'Recepção'):
            return obj.status == 'ABERTO'

        # 3. Regras para Membro do Gabinete e Secretária
        if is_in_group(user, 'Membro do Gabinete') or is_in_group(user, 'Secretária'):
            # Primeiro, o atendimento DEVE pertencer ao seu gabinete.
            if not (hasattr(user, 'perfil') and obj.conta in user.perfil.contas.all()):
                return False # Se não for, bloqueia.

            # Se for do seu gabinete, pode ver/editar se for responsável, co-responsável OU se não houver responsável.
            if obj.responsavel is None:
                return True
            if obj.responsavel == user:
                return True
            if hasattr(obj, 'responsaveis_compartilhados'):
                return obj.responsaveis_compartilhados.filter(pk=user.pk).exists()
            return False

        # 4. Nega todos os outros casos.
        return False
    
class CanViewAtendimentoReports(BasePermission):
    """
    Permite acesso aos relatórios de Atendimento.
    (Membros, Secretárias e Admin)
    """
    def has_permission(self, request, view):
        user = request.user
        return (
            user.is_superuser
            or is_in_group(user, 'Membro do Gabinete')
            or is_in_group(user, 'Secretária')
        )

class CanViewAgendaReports(BasePermission):
    """
    Permite acesso aos relatórios de Agenda.
    (Apenas Secretárias e Admin)
    """
    def has_permission(self, request, view):
        user = request.user
        return user.is_superuser or is_in_group(user, 'Secretária')
    
# Adicione esta nova classe ao final de permissions.py

def _grupos_crud_contatos():
    """Grupos que têm CRUD em contatos por conta compartilhada."""
    return ['Recepção', 'Membro do Gabinete', 'Secretária']


class CanAccessContacts(BasePermission):
    """
    Acesso à página/recursos de Contatos (Agenda de Contatos).
    Superadmin: acesso total. Recepção, Membro do Gabinete e Secretária: acesso
    aos contatos vinculados a pelo menos uma Conta do perfil do usuário.
    """
    def has_permission(self, request, view):
        user = request.user
        if user.is_superuser:
            return True
        return is_in_group(user, _grupos_crud_contatos())


class CanEditMunicipeDetails(BasePermission):
    """
    Permissão de edição (CRUD) para um contato específico.
    Superadmin: sempre. Demais: apenas se compartilhar pelo menos uma Conta com o Munícipe.
    """
    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser:
            return True
        if not hasattr(user, 'perfil') or not is_in_group(user, _grupos_crud_contatos()):
            return False
        user_contas = set(user.perfil.contas.all())
        municipe_contas = set(obj.contas.all())
        return not user_contas.isdisjoint(municipe_contas)

class CanManageCheckIn(BasePermission):
    """
    Permissão para gerenciar Registros de Visita (Check-ins).
    Apenas Recepção e Superusuários (listar, editar, excluir).
    """

    def has_permission(self, request, view):
        user = request.user
        return user.is_superuser or is_in_group(user, 'Recepção')


class CanCreateCheckIn(BasePermission):
    """
    Permissão para CRIAR Registros de Visita (Check-ins).
    Qualquer usuário autenticado pode criar (ex.: via tela de Triagem).
    Edição e exclusão continuam restritas a Recepção/Superusuário.
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

class CanCreateGoogleEvent(BasePermission):
    """
    Permissão para criar um evento no Google Agenda.
    Apenas Secretárias e Superusuários.
    """
    def has_permission(self, request, view):
        user = request.user
        return user.is_superuser or is_in_group(user, 'Secretária')


class CanViewSharedAgenda(BasePermission):
    """
    Permite acesso à agenda compartilhada se o usuário:
    1. For do grupo 'Membro do Gabinete'.
    2. Tiver a flag 'pode_visualizar_agendas_compartilhadas' marcada em seu perfil.
    3. Estiver vinculado à conta que está tentando acessar.
    """
    def has_permission(self, request, view):
        user = request.user
        conta_id_alvo = view.kwargs.get('conta_id')

        if not user.is_authenticated or not hasattr(user, 'perfil') or not conta_id_alvo:
            return False

        # Verifica as 3 condições
        tem_grupo = is_in_group(user, 'Membro do Gabinete')
        tem_flag = user.perfil.pode_visualizar_agendas_compartilhadas
        esta_vinculado_a_conta = user.perfil.contas.filter(id=conta_id_alvo).exists()

        return tem_grupo and tem_flag and esta_vinculado_a_conta

class CanManageReservas(BasePermission):
    """
    Permite que Membros de Gabinete e Secretárias gerenciem Reservas de Espaço.
    """
    def has_permission(self, request, view):
        user = request.user
        return (
            user.is_superuser or
            is_in_group(user, ['Secretária', 'Membro do Gabinete'])
        )

class CanAccessEspaco(BasePermission):
    """
    Permite acesso a um Espaço se o usuário compartilhar
    pelo menos uma Conta com aquele Espaço.
    """
    def has_object_permission(self, request, view, obj):
        user = request.user

        if user.is_superuser:
            return True

        if hasattr(user, 'perfil'):
            user_contas = set(user.perfil.contas.all())
            espaco_contas = set(obj.contas.all())
            # Retorna True se houver pelo menos uma conta em comum
            return not user_contas.isdisjoint(espaco_contas)

        return False

class CanManageLembretes(BasePermission):
    """
    Permissão para gerenciar Lembretes.
    Apenas Secretárias e Superusuários.
    """
    def has_permission(self, request, view):
        user = request.user
        return user.is_superuser or is_in_group(user, 'Secretária')
