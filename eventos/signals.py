from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Evento, EventoChecklist, ChecklistItem, EventoChecklistItemStatus

@receiver(post_save, sender=Evento)
def criar_checklist_para_novo_evento(sender, instance, created, **kwargs):
    """
    Cria um EventoChecklist e seus itens sempre que um novo Evento é criado.
    """
    if created:
        # 1. Cria o objeto principal do checklist vinculado ao evento
        checklist_do_evento = EventoChecklist.objects.create(evento=instance)

        # 2. Pega todos os itens mestres universais
        itens_mestre = ChecklistItem.objects.all()

        # 3. Cria um status para cada item mestre dentro do novo checklist
        for item in itens_mestre:
            EventoChecklistItemStatus.objects.create(
                evento_checklist=checklist_do_evento,
                item_mestre=item
            )