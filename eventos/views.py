# eventos/views.py
import uuid
import csv
import os
from datetime import datetime, time
from openpyxl import Workbook
from weasyprint import HTML
from django.http import HttpResponse
from django.db.models import Count, Q, F
from django.shortcuts import render, get_object_or_404, redirect
from django.db import transaction, models
from django.utils import timezone
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.conf import settings
import weasyprint
from rest_framework.views import APIView
from rest_framework import viewsets, permissions, serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .relatorios import gerar_pdf_checklist, gerar_pdf_eventos_periodo
from .models import Evento, ListaPresenca, EventoChecklist, Convidado, Comunicacao, Destinatario, LogDeEnvio, EventoChecklistItemStatus, ChecklistItem, MailingList, Municipe
from atendimentos.models import Municipe, CategoriaContato, PerfilMunicipe 
from .forms import ListaPresencaForm
from .serializers import EventoSerializer, ConvidadoSerializer, ComunicacaoSerializer, DestinatarioSerializer, LogDeEnvioSerializer, ListaPresencaSerializer, EventoChecklistSerializer, EventoChecklistItemStatusSerializer, ChecklistItemSerializer, MailingListSerializer, MunicipeForConvidadoSerializer
from .utils import gerar_e_enviar_certificado
from .permissions import PodeGerenciarEventos
from eventos.tasks import enviar_comunicacao_em_massa, gerar_e_enviar_certificado
from atendimentos.management.commands.verificar_duplicatas import normalizar_nome_para_conjunto

def get_image_path(image_field):
    if not image_field:
        return None
    
    try:
        path = image_field.path 
        if os.path.exists(path):
            return f'file://{path}'
    except Exception:
        pass
    
    return None

class EventoViewSet(viewsets.ModelViewSet):
    serializer_class = EventoSerializer
    permission_classes = [PodeGerenciarEventos]

    def get_queryset(self):
        # This function is already correct
        user = self.request.user
        if user.is_superuser:
            return Evento.objects.all().order_by('-data_evento')
        if hasattr(user, 'perfil'):
            contas_do_usuario = user.perfil.contas.all()
            if contas_do_usuario.exists():
                return Evento.objects.filter(conta__in=contas_do_usuario).order_by('-data_evento')
        return Evento.objects.none()

    def perform_create(self, serializer):
        # This function is also correct
        conta_do_usuario = self.request.user.perfil.contas.first()
        if conta_do_usuario:
            serializer.save(conta=conta_do_usuario)
        else:
            raise serializers.ValidationError("O usuário não está associado a nenhuma conta.")

    @action(detail=True, methods=['post'], url_path='adicionar-por-categoria')
    def adicionar_por_categoria(self, request, pk=None):
        """
        Adiciona todos os munícipes de uma categoria como convidados a este evento,
        ignorando aqueles que já foram convidados.
        """
        # --- DEBUG: Verificando se a nova função está sendo executada ---
        print("--- EXECUTANDO A NOVA AÇÃO 'adicionar_por_categoria' ---")
        
        evento = self.get_object()
        categoria_id = request.data.get('categoria_id')
        
        print(f"Evento ID: {evento.id}, Categoria ID recebida: {categoria_id}")

        if not categoria_id:
            return Response(
                {'error': 'O ID da categoria é obrigatório.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            from atendimentos.models import PerfilMunicipe
            perfis_ja_convidados = set(
                Convidado.objects.filter(evento=evento).values_list('perfil_id', flat=True)
            )
            # Perfis da conta do evento com a categoria selecionada
            perfis_para_adicionar = PerfilMunicipe.objects.filter(
                conta=evento.conta, categoria_id=categoria_id
            ).select_related('municipe')
            novos_convidados = []
            for perfil in perfis_para_adicionar:
                if perfil.id not in perfis_ja_convidados:
                    novos_convidados.append(Convidado(evento=evento, perfil=perfil))
                    perfis_ja_convidados.add(perfil.id)
            if novos_convidados:
                Convidado.objects.bulk_create(novos_convidados)

            return Response(
                {'status': f'{len(novos_convidados)} novo(s) convidado(s) da categoria foram adicionado(s).'},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            print(f"ERRO NA AÇÃO: {e}") # Debug do erro
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='adicionar-destinatarios-por-categoria')
    def adicionar_destinatarios_por_categoria(self, request, pk=None):
        """
        Adiciona todos os munícipes de uma categoria como DESTINATÁRIOS a este evento,
        ignorando aqueles que já estão na lista.
        """
        evento = self.get_object()
        categoria_id = request.data.get('categoria_id')

        if not categoria_id:
            return Response(
                {'error': 'O ID da categoria é obrigatório.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # 1. Pega os IDs dos munícipes que já são destinatários.
            destinatarios_existentes_ids = Destinatario.objects.filter(evento=evento).values_list('municipe_id', flat=True)

            # 2. Encontra munícipes com perfil na conta do evento e categoria selecionada
            municipes_para_adicionar = Municipe.objects.filter(
                perfis__conta=evento.conta,
                perfis__categoria_id=categoria_id
            ).exclude(
                id__in=destinatarios_existentes_ids
            ).filter(
                Q(emails__isnull=False) & ~Q(emails__exact='[]')
            ).distinct()

            # 3. Cria os novos objetos Destinatario em massa.
            novos_destinatarios = [
                Destinatario(evento=evento, municipe=municipe)
                for municipe in municipes_para_adicionar
            ]
            
            if novos_destinatarios:
                Destinatario.objects.bulk_create(novos_destinatarios)

            return Response(
                {'status': f'{len(novos_destinatarios)} novo(s) destinatário(s) da categoria foram adicionado(s).'},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path='gerar-relatorio-periodo')
    def gerar_relatorio_periodo(self, request):
        data_inicio_str = request.query_params.get('data_inicio')
        data_fim_str = request.query_params.get('data_fim')

        if not data_inicio_str or not data_fim_str:
            return Response({'error': 'As datas de início e fim são obrigatórias.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
            data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({'error': 'Formato de data inválido. Use AAAA-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        start_datetime = datetime.combine(data_inicio, time.min)
        end_datetime = datetime.combine(data_fim, time.max)
        
        # Filtra os eventos e ordena por data
        queryset = self.get_queryset().filter(data_evento__range=[start_datetime, end_datetime]).order_by('data_evento')

        # Contexto que será enviado para o template HTML
        context = {
            'eventos': queryset,
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'data_emissao': timezone.now(),
            'hoje': timezone.now(),
            'logo_url': request.build_absolute_uri('/static/images/logo-siga-gab.png')
        }
        
        # Renderiza o template HTML para uma string
        html_string = render_to_string('eventos/relatorio_eventos.html', context)
        
        # Gera o PDF a partir do HTML
        pdf_file = HTML(string=html_string).write_pdf()
        
        # Cria a resposta HTTP
        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="relatorio_eventos_{data_inicio_str}_a_{data_fim_str}.pdf"'
        
        return response

    @action(detail=True, methods=['get'], url_path='relatorio-convidados-presentes')
    def relatorio_convidados_presentes(self, request, pk=None):
        """
        Gera um relatório em PDF com a lista de TODOS os convidados do evento,
        respeitando a ordem manual.
        """
        try:
            evento = self.get_object()
            conta = evento.conta
            todos_convidados = evento.convidados.select_related('perfil__municipe').order_by('ordem', 'perfil__municipe__nome_completo')
            logo_url = get_image_path(conta.logo_conta) or ''
            brasao_url = get_image_path(conta.brasao_instituicao) or ''

            convidados_com_foto = []
            for convidado in todos_convidados:
                convidado.foto_url_absoluta = get_image_path(convidado.perfil.municipe.foto)
                convidados_com_foto.append(convidado)

            context = {
                'evento': evento,
                'convidados': convidados_com_foto,
                'logo_url': logo_url,
                'brasao_url': brasao_url,
                'data_emissao': timezone.now(),
            }

            html_string = render_to_string('eventos/relatorio_convidados.html', context)
            pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()

            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="relatorio_convidados_{evento.nome}.pdf"'
            
            return response
        except Exception as e:
            print(f"Erro ao gerar relatório de convidados: {e}")
            return Response({'error': 'Ocorreu um erro interno ao gerar o relatório.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='relatorio-crachas')
    def relatorio_crachas(self, request, pk=None):
        """
        Gera um PDF de crachás para uma lista de IDs de convidados selecionados.
        """
        convidado_ids = request.data.get('convidado_ids')
        if not convidado_ids or not isinstance(convidado_ids, list):
            return Response({'error': 'Uma lista de IDs de convidados é obrigatória.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            evento = self.get_object()
            convidados_selecionados = Convidado.objects.select_related('perfil__municipe').filter(id__in=convidado_ids, evento=evento).order_by('ordem')
            conta = evento.conta
            logo_url = get_image_path(conta.logo_conta) or ''
            brasao_url = get_image_path(conta.brasao_instituicao) or ''
            convidados_com_foto = []
            for convidado in convidados_selecionados:
                convidado.foto_url_absoluta = get_image_path(convidado.perfil.municipe.foto)
                convidados_com_foto.append(convidado)

            context = {
                'evento': evento,
                'convidados': convidados_com_foto,
                'logo_url': logo_url,
                'brasao_url': brasao_url,
            }

            html_string = render_to_string('eventos/relatorio_crachas.html', context)
            pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()

            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="crachas_{evento.nome}.pdf"'
            
            return response
        except Exception as e:
            print(f"Erro ao gerar crachás: {e}")
            return Response({'error': 'Ocorreu um erro interno ao gerar o relatório.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='relatorio-prismas')
    def relatorio_prismas(self, request, pk=None):
        """
        Gera um PDF de prismas de mesa para uma lista de IDs de convidados selecionados.
        """
        convidado_ids = request.data.get('convidado_ids')

        if not convidado_ids or not isinstance(convidado_ids, list):
            return Response({'error': 'Uma lista de IDs de convidados é obrigatória.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            evento = self.get_object()
            
            convidados_selecionados = Convidado.objects.filter(id__in=convidado_ids, evento=evento).order_by('ordem')

            context = {
                'evento': evento,
                'convidados': convidados_selecionados,
            }

            html_string = render_to_string('eventos/relatorio_prismas.html', context)
            pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()

            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="prismas_{evento.nome}.pdf"'
            
            return response
        except Exception as e:
            print(f"Erro ao gerar prismas: {e}")
            return Response({'error': 'Ocorreu um erro interno ao gerar o relatório.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

def registrar_presenca(request, evento_id):
    evento = get_object_or_404(Evento, id=evento_id)

    # 1. VERIFICAÇÃO DE SEGURANÇA
    if not evento.ativo:
        # Aqui você pode renderizar uma página de "Evento Inativo"
        return render(request, 'eventos/evento_inativo.html', {'evento': evento})

    if request.method == 'POST':
        form = ListaPresencaForm(request.POST)
        if form.is_valid():
            dados = form.cleaned_data
            conta_do_evento = evento.conta

            try:
                # Garante que todas as operações no banco sejam feitas com sucesso ou nenhuma será.
                with transaction.atomic():
                    # 2. LÓGICA DE MUNICIPE (CRIAR OU ATUALIZAR)
                    # Busca um munícipe com o mesmo telefone NA MESMA CONTA do evento.
                    municipe, criado = Municipe.objects.update_or_create(
                        telefone=dados['telefone'],
                        conta=conta_do_evento,
                        defaults={
                            'nome_completo': dados['nome_completo'],
                            'data_nascimento': dados.get('data_nascimento'),
                            # Adiciona o email ao Municipe se não existir um
                            'emails': dados.get('email')
                        }
                    )
                    
                    # 3. REGISTRA A PRESENÇA
                    # Impede o registro duplicado
                    if ListaPresenca.objects.filter(evento=evento, municipe=municipe).exists():
                        # Pode redirecionar para uma página de erro "Presença já registrada"
                        form.add_error(None, 'Sua presença neste evento já foi registrada.')
                        return render(request, 'eventos/registrar_presenca.html', {'form': form, 'evento': evento})

                    presenca = ListaPresenca.objects.create(
                        evento=evento,
                        municipe=municipe,
                        nome_completo=dados['nome_completo'],
                        telefone=dados['telefone'],
                        email=dados.get('email'),
                        instituicao_orgao=dados.get('instituicao_orgao')
                    )

                # 4. GERA E ENVIA O CERTIFICADO
                if presenca.email:
                    # Chamar uma função auxiliar para fazer o trabalho pesado
                    # gerar_e_enviar_certificado(presenca)
                    pass

                # Redireciona para uma página de sucesso
                return redirect('presenca_sucesso')

            except Exception as e:
                # Em caso de erro, informa o usuário.
                form.add_error(None, f"Ocorreu um erro ao registrar sua presença: {e}")

    else:
        form = ListaPresencaForm()

    return render(request, 'eventos/registrar_presenca.html', {'form': form, 'evento': evento})

def presenca_sucesso(request):
    return render(request, 'eventos/presenca_sucesso.html')

def preencher_checklist(request, token):
    # Busca o checklist pelo token ou retorna um erro 404 se não encontrar
    checklist = get_object_or_404(EventoChecklist, token=token)

    # Por enquanto, apenas renderizamos uma página simples.
    # A lógica do formulário virá depois.
    context = {
        'checklist': checklist,
        'evento': checklist.evento,
    }
    return render(request, 'eventos/formulario_checklist.html', context)

class ConvidadoViewSet(viewsets.ModelViewSet):
    """
    API para gerenciar os Convidados de um evento.
    - Filtra por evento: /api/convidados/?evento=1
    """
    serializer_class = ConvidadoSerializer
    permission_classes = [PodeGerenciarEventos]

    def get_queryset(self):
        # A ordenação agora é feita pelo 'ordering' no modelo, então o queryset já vem ordenado.
        usuario = self.request.user
        qs = Convidado.objects.none()

        if usuario.is_superuser:
            qs = Convidado.objects.all()
        elif hasattr(usuario, 'perfil'):
            contas_do_usuario = usuario.perfil.contas.all()
            qs = Convidado.objects.filter(evento__conta__in=contas_do_usuario)
        
        evento_id = self.request.query_params.get('evento')
        if evento_id:
            return qs.filter(evento_id=evento_id).select_related('perfil__municipe', 'perfil__conta').order_by('perfil__municipe__nome_completo')
        return qs.select_related('perfil__municipe', 'perfil__conta').order_by('perfil__municipe__nome_completo')

    def perform_create(self, serializer):
        # Define a ordem inicial do novo convidado
        # Esta função agora funcionará porque 'models' foi importado.
        evento_id = serializer.validated_data['evento'].id
        maior_ordem = Convidado.objects.filter(evento_id=evento_id).aggregate(models.Max('ordem'))['ordem__max'] or 0
        serializer.save(ordem=0)

    @action(detail=False, methods=['post'], url_path='reorder')
    def reorder(self, request):
        """
        Recebe uma lista de IDs de convidados na nova ordem e atualiza o campo 'ordem'.
        """
        ordered_ids = request.data.get('ordered_ids')
        evento_id = request.query_params.get('evento')

        if not ordered_ids or not evento_id:
            return Response({'error': 'Lista de IDs e ID do evento são obrigatórios.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            for index, convidado_id in enumerate(ordered_ids):
                Convidado.objects.filter(id=convidado_id, evento_id=evento_id).update(ordem=index)
        
        return Response({'status': 'Ordem dos convidados atualizada com sucesso.'})

    @action(detail=True, methods=['post'], url_path='update-status')
    def update_status(self, request, pk=None):
        """
        Atualiza o status de um convidado (ex: para 'presente').
        """
        try:
            convidado = self.get_object()
            novo_status = request.data.get('status')

            # Valida se o status enviado é uma das opções válidas
            status_choices = [choice[0] for choice in Convidado.STATUS_CHOICES]
            if novo_status not in status_choices:
                return Response({'error': f'Status inválido. Use um de: {status_choices}'}, status=status.HTTP_400_BAD_REQUEST)

            convidado.status = novo_status
            # Atualiza a data do check-in se o status for 'presente'
            if novo_status == 'presente' and not convidado.data_checkin:
                convidado.data_checkin = timezone.now()
            elif novo_status != 'presente':
                convidado.data_checkin = None # Limpa a data se não estiver mais presente

            convidado.save()
            return Response({'status': 'Status atualizado com sucesso.'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ComunicacaoViewSet(viewsets.ModelViewSet):
    serializer_class = ComunicacaoSerializer
    permission_classes = [PodeGerenciarEventos]
    filterset_fields = ['evento']

    def get_queryset(self):
        usuario = self.request.user
        qs = Comunicacao.objects.none()
        
        if usuario.is_superuser:
            qs = Comunicacao.objects.all()
        elif hasattr(usuario, 'perfil'):
            contas_do_usuario = usuario.perfil.contas.all()
            qs = Comunicacao.objects.filter(evento__conta__in=contas_do_usuario)

        evento_id = self.request.query_params.get('evento')
        if evento_id:
            return qs.filter(evento_id=evento_id)
        
        return qs.order_by('-data_criacao')

        # Se não for superusuário, filtramos pelas contas do perfil.
        if hasattr(usuario, 'perfil'):
            contas_do_usuario = usuario.perfil.contas.all()
            if contas_do_usuario.exists():
                # Retorna todas as comunicações cujo evento pertence a uma das contas do usuário.
                # Esta é a lógica que funciona tanto para a lista quanto para os detalhes.
                return Comunicacao.objects.filter(evento__conta__in=contas_do_usuario).order_by('-data_criacao')
        
        # Se o usuário não for superuser ou não tiver contas, não retorna nada.
        return Comunicacao.objects.none()

    def perform_create(self, serializer):
        evento_id = self.request.data.get('evento_id')
        try:
            evento = Evento.objects.get(id=evento_id)
            if not self.request.user.is_superuser and evento.conta not in self.request.user.perfil.contas.all():
                 raise serializers.ValidationError("Você não tem permissão para criar uma comunicação para este evento.")
            serializer.save(evento=evento)
        except Evento.DoesNotExist:
            raise serializers.ValidationError("O evento especificado não existe.")

    @action(detail=True, methods=['post'], url_path='adicionar-por-categoria')
    def adicionar_por_categoria(self, request, pk=None):
        """
        Adiciona todos os munícipes de uma categoria como DESTINATÁRIOS a esta comunicação.
        """
        comunicacao = self.get_object() # Agora pega a instância da Comunicação
        categoria_id = request.data.get('categoria_id')

        if not categoria_id:
            return Response({'error': 'ID da categoria é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Pega os IDs dos munícipes que já são destinatários DESTA comunicação.
        destinatarios_existentes_ids = Destinatario.objects.filter(comunicacao=comunicacao).values_list('municipe_id', flat=True)

        # 2. Encontra munícipes com perfil na conta do evento e categoria selecionada
        evento_ref = comunicacao.evento
        municipes_para_adicionar = Municipe.objects.filter(
            perfis__conta=evento_ref.conta,
            perfis__categoria_id=categoria_id
        ).exclude(
            id__in=destinatarios_existentes_ids
        ).filter(
            Q(emails__isnull=False) & ~Q(emails__exact='[]')
        ).distinct()

        # 3. Cria os novos objetos Destinatario em massa.
        novos_destinatarios = [
            Destinatario(comunicacao=comunicacao, municipe=municipe)
            for municipe in municipes_para_adicionar
        ]
        
        if novos_destinatarios:
            Destinatario.objects.bulk_create(novos_destinatarios)

        return Response(
            {'status': f'{len(novos_destinatarios)} novo(s) destinatário(s) com e-mail foram adicionado(s).'},
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'], url_path='adicionar-por-mailing-list')
    def adicionar_por_mailing_list(self, request, pk=None):
        """
        Adiciona todos os contatos de uma Mailing List como destinatários.
        """
        comunicacao = self.get_object()
        mailing_list_id = request.data.get('mailing_list_id')

        if not mailing_list_id:
            return Response({'error': 'O ID da lista de mailing é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            mailing_list = MailingList.objects.get(id=mailing_list_id, conta=comunicacao.evento.conta)
        except MailingList.DoesNotExist:
            return Response({'error': 'Lista de mailing não encontrada.'}, status=status.HTTP_404_NOT_FOUND)

        destinatarios_existentes_ids = Destinatario.objects.filter(comunicacao=comunicacao).values_list('municipe_id', flat=True)
        
        municipes_para_adicionar = mailing_list.municipes.exclude(id__in=destinatarios_existentes_ids)

        novos_destinatarios = [
            Destinatario(comunicacao=comunicacao, municipe=municipe)
            for municipe in municipes_para_adicionar
        ]
        
        if novos_destinatarios:
            Destinatario.objects.bulk_create(novos_destinatarios)

        return Response(
            {'status': f'{len(novos_destinatarios)} novo(s) destinatário(s) da lista "{mailing_list.nome}" foram adicionado(s).'},
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['get'], url_path='relatorio-pdf')
    def relatorio_pdf(self, request, pk=None):
        """
        Gera PDF com a relação de contatos selecionados para envio da comunicação e resumo.
        """
        comunicacao = self.get_object()
        from django.utils import timezone as tz
        from django.template.loader import render_to_string

        evento = comunicacao.evento
        destinatarios_qs = comunicacao.destinatarios.select_related('municipe').all().order_by('municipe__nome_completo')

        def get_email_principal(municipe):
            emails = getattr(municipe, 'emails', None)
            if not emails or not isinstance(emails, list):
                return None
            principal = next((e for e in emails if isinstance(e, dict) and e.get('tipo') == 'principal'), None)
            if principal and principal.get('email'):
                return principal['email']
            return emails[0].get('email') if emails and isinstance(emails[0], dict) else None

        destinatarios = [
            {
                'municipe': d.municipe,
                'email_principal': get_email_principal(d.municipe),
            }
            for d in destinatarios_qs
        ]

        context = {
            'comunicacao': comunicacao,
            'evento': evento,
            'destinatarios': destinatarios,
            'data_emissao': tz.now(),
        }

        html_string = render_to_string('eventos/relatorio_comunicacao.html', context)
        pdf_file = HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf()

        response = HttpResponse(pdf_file, content_type='application/pdf')
        filename = f"relatorio_comunicacao_{comunicacao.id}_{tz.now().strftime('%Y%m%d_%H%M')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    @action(detail=True, methods=['post'], url_path='enviar')
    def enviar(self, request, pk=None):
        """
        Esta ação é o gatilho para o envio em massa.
        Ela adiciona a tarefa à fila do Celery e retorna uma resposta imediata.
        """
        comunicacao = self.get_object()

        if comunicacao.status == 'enviado':
            return Response(
                {'error': 'Esta comunicação já foi enviada.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Adiciona a tarefa à fila do Celery
        enviar_comunicacao_em_massa.delay(comunicacao.id)

        # Atualiza o status da comunicação
        comunicacao.status = 'enviado'
        comunicacao.data_envio = timezone.now()
        comunicacao.save()

        return Response(
            {'status': 'A comunicação foi adicionada à fila de envio.'},
            status=status.HTTP_202_ACCEPTED
        )

class DestinatarioViewSet(viewsets.ModelViewSet):
    serializer_class = DestinatarioSerializer
    permission_classes = [PodeGerenciarEventos]

    def get_queryset(self):
        """
        Esta versão corrigida retorna a base de destinatários permitidos para o usuário,
        filtrando corretamente pela comunicação.
        """
        usuario = self.request.user
        qs = Destinatario.objects.none()

        # Define a base de destinatários que o usuário pode ver
        if usuario.is_superuser:
            qs = Destinatario.objects.all()
        elif hasattr(usuario, 'perfil'):
            contas_do_usuario = usuario.perfil.contas.all()
            # O caminho correto: Destinatario -> comunicacao -> evento -> conta
            qs = Destinatario.objects.filter(comunicacao__evento__conta__in=contas_do_usuario)
        
        # --- A CORREÇÃO PRINCIPAL ---
        # Agora filtramos pelo parâmetro 'comunicacao' que o frontend vai enviar
        comunicacao_id = self.request.query_params.get('comunicacao')
        if comunicacao_id:
            return qs.filter(comunicacao_id=comunicacao_id)
            
        # Retorna a base de permissão se nenhum filtro específico for aplicado
        return qs.select_related('municipe')        

class LogDeEnvioViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LogDeEnvioSerializer
    permission_classes = [PodeGerenciarEventos]
    filterset_fields = ['comunicacao']

    def get_queryset(self):
        # CORREÇÃO 3: Aplicando o seu padrão para Logs.
        usuario = self.request.user
        qs = LogDeEnvio.objects.none()

        if usuario.is_superuser:
            qs = LogDeEnvio.objects.all()
        elif hasattr(usuario, 'perfil'):
            contas_do_usuario = usuario.perfil.contas.all()
            qs = LogDeEnvio.objects.filter(comunicacao__evento__conta__in=contas_do_usuario)
        
        # O frontend envia '?comunicacao=ID', então filtramos por isso.
        comunicacao_id = self.request.query_params.get('comunicacao')
        if comunicacao_id:
            return qs.filter(comunicacao_id=comunicacao_id)

        return qs.order_by('-data_envio')

class PublicCheckInView(APIView):
    """
    View pública para o formulário de check-in via QR Code.
    Não exige autenticação.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, conta_id, *args, **kwargs):
        """
        Retorna os detalhes do evento ativo para o formulário.
        """
        try:
            evento_ativo = Evento.objects.get(conta_id=conta_id, ativo=True)
            conta = evento_ativo.conta
            logo_url = request.build_absolute_uri(conta.logo_conta.url) if conta.logo_conta else None
            brasao_url = request.build_absolute_uri(conta.brasao_instituicao.url) if conta.brasao_instituicao else None
            
            return Response({
                'evento_id': evento_ativo.id,
                'evento_nome': evento_ativo.nome,
                'evento_data': evento_ativo.data_evento.strftime('%d de %B de %Y'),
                'logo_url': logo_url,
                'brasao_url': brasao_url,
            })
        except Evento.DoesNotExist:
            return Response({'error': 'Nenhum evento ativo encontrado para esta conta.'}, status=status.HTTP_404_NOT_FOUND)

    def post(self, request, conta_id, *args, **kwargs):
        """
        Processa o formulário de check-in com lógica inteligente de duplicatas.
        """
        try:
            evento_ativo = Evento.objects.get(conta_id=conta_id, ativo=True)
            conta = evento_ativo.conta
        except Evento.DoesNotExist:
            return Response({'error': 'Nenhum evento ativo para fazer check-in.'}, status=status.HTTP_400_BAD_REQUEST)

        # Dados do formulário
        nome_completo = request.data.get('nome_completo', '').strip()
        telefone = request.data.get('telefone', '').strip()
        email = request.data.get('email', '').strip().lower()
        data_nascimento_str = request.data.get('data_nascimento') 
        orgao = request.data.get('orgao')
        
        if not nome_completo or not telefone:
             return Response({'error': 'Nome e telefone são obrigatórios.'}, status=status.HTTP_400_BAD_REQUEST)

        municipe_encontrado = None
        
        # --- LÓGICA DE BUSCA INTELIGENTE ---
        # 1. Busca por contatos que compartilham o mesmo telefone ou e-mail na mesma conta
        candidatos = Municipe.objects.filter(
            Q(telefones__contains=[{'numero': telefone}]) | Q(emails__contains=[{'email': email}]),
            contas=conta
        ).distinct()

        if candidatos.exists():
            nome_form_normalizado = normalizar_nome_para_conjunto(nome_completo)
            for candidato in candidatos:
                nome_candidato_normalizado = normalizar_nome_para_conjunto(candidato.nome_completo)
                # 2. Compara os nomes usando a lógica de subconjunto
                if nome_form_normalizado.issubset(nome_candidato_normalizado) or nome_candidato_normalizado.issubset(nome_form_normalizado):
                    municipe_encontrado = candidato
                    break # Para no primeiro candidato compatível encontrado

        with transaction.atomic():
            if municipe_encontrado:
                # --- LÓGICA DE ATUALIZAÇÃO (COMPLEMENTAR DADOS) ---
                municipe = municipe_encontrado
                dados_atualizados = False

                # Complementa o telefone se for novo
                telefones_existentes = {t.get('numero') for t in (municipe.telefones or []) if isinstance(t, dict)}
                if telefone and telefone not in telefones_existentes:
                    municipe.telefones.append({'tipo': 'celular', 'numero': telefone})
                    dados_atualizados = True

                # Complementa o e-mail se for novo
                emails_existentes = {e.get('email', '').lower() for e in (municipe.emails or []) if isinstance(e, dict)}
                if email and email not in emails_existentes:
                    municipe.emails.append({'tipo': 'principal', 'email': email})
                    dados_atualizados = True
                
                if dados_atualizados:
                    municipe.save()
            
            else:
                # --- LÓGICA DE CRIAÇÃO (SE NENHUM CONTATO FOR ENCONTRADO) ---
                categoria_municipe, _ = CategoriaContato.objects.get_or_create(nome="MUNÍCIPE")
                municipe = Municipe.objects.create(
                    nome_completo=nome_completo.upper(),
                    telefones=[{'tipo': 'principal', 'numero': telefone}],
                    emails=[{'tipo': 'principal', 'email': email}] if email else [],
                    orgao=orgao.upper() if orgao else None,
                )
                municipe.contas.add(conta)
                PerfilMunicipe.objects.get_or_create(
                    municipe=municipe, conta=conta,
                    defaults={'cargo': None, 'instituicao': orgao, 'categoria': categoria_municipe, 'ativo': True}
                )

            # Registra a presença
            presenca, presenca_criada = ListaPresenca.objects.get_or_create(
                evento=evento_ativo,
                municipe=municipe,
                defaults={
                    'nome_completo': municipe.nome_completo,
                    'telefone': telefone,
                    'email': email,
                    'instituicao_orgao': orgao
                }
            )

            if not presenca_criada:
                return Response({'status': 'Sua presença neste evento já foi registrada.'}, status=status.HTTP_200_OK)

            if presenca.email:
                gerar_e_enviar_certificado.delay(presenca.id)

        return Response({'status': 'Presença registrada com sucesso!'}, status=status.HTTP_201_CREATED)

class ListaPresencaViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API para visualizar a Lista de Presença de um evento.
    """
    serializer_class = ListaPresencaSerializer
    permission_classes = [PodeGerenciarEventos]
    # Manter o filterset_fields é uma boa prática, embora vamos controlar o filtro manualmente.
    filterset_fields = ['evento']

    def get_queryset(self):
        """
        Garante que a lista de presença seja filtrada tanto pelas permissões do usuário
        quanto pelo evento específico solicitado na URL.
        """
        usuario = self.request.user
        qs = ListaPresenca.objects.none()

        # 1. Define o queryset base de acordo com as permissões do usuário
        if usuario.is_superuser:
            qs = ListaPresenca.objects.all()
        elif hasattr(usuario, 'perfil'):
            contas_do_usuario = usuario.perfil.contas.all()
            qs = ListaPresenca.objects.filter(evento__conta__in=contas_do_usuario)

        # 2. APLICA O FILTRO DO EVENTO ESPECÍFICO
        # Pega o 'evento' da URL (ex: ?evento=11) que o frontend envia
        evento_id = self.request.query_params.get('evento')
        if evento_id:
            # Filtra o queryset base para retornar apenas as presenças do evento solicitado
            return qs.filter(evento_id=evento_id).order_by('-data_registro')
        
        # Se nenhum evento for especificado, retorna o queryset baseado na permissão
        # (geralmente não deve acontecer na tela de detalhes do evento)
        return qs.order_by('-data_registro')

    @action(detail=False, methods=['get'], url_path='exportar-excel')
    def exportar_excel(self, request):
        evento_id = request.query_params.get('evento')
        if not evento_id:
            return Response({"error": "O ID do evento é obrigatório."}, status=status.HTTP_400_BAD_REQUEST)

        # O queryset base agora é filtrado corretamente pela função get_queryset
        queryset = self.get_queryset().filter(evento_id=evento_id)
        
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Lista de Presença"

        # Cabeçalhos
        headers = ["Nome Completo", "Telefone", "E-mail", "Instituição/Órgão", "Data do Registro"]
        sheet.append(headers)

        # Adiciona os dados
        for item in queryset:
            sheet.append([
                item.nome_completo,
                item.telefone,
                item.email,
                item.instituicao_orgao,
                item.data_registro.strftime('%d/%m/%Y %H:%M:%S') if item.data_registro else ''
            ])

        # Prepara a resposta HTTP
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="lista_presenca_evento_{evento_id}.xlsx"'
        workbook.save(response)
        
        return response

class EventoChecklistViewSet(viewsets.ModelViewSet):
    """
    API para gerenciar os Checklists dos Eventos.
    """
    serializer_class = EventoChecklistSerializer
    permission_classes = [PodeGerenciarEventos]
    
    def get_queryset(self):
        """
        Filtra o queryset com base na ação (list vs. detail) e permissões do usuário.
        """
        usuario = self.request.user
        qs = EventoChecklist.objects.none() # Começa com um queryset vazio

        # Define a base de checklists que o usuário pode acessar
        if usuario.is_superuser:
            qs = EventoChecklist.objects.all()
        elif hasattr(usuario, 'perfil'):
            contas_do_usuario = usuario.perfil.contas.all()
            qs = EventoChecklist.objects.filter(evento__conta__in=contas_do_usuario)
            
        # Se a ação for a 'list', filtramos pelo parâmetro 'evento'.
        if self.action == 'list':
            evento_id = self.request.query_params.get('evento')
            if evento_id:
                return qs.filter(evento_id=evento_id)
            # Se for a lista, mas sem evento, não retorna nada.
            return EventoChecklist.objects.none()
            
        return qs

    @action(detail=True, methods=['get'], url_path='gerar-relatorio')
    def gerar_relatorio(self, request, pk=None):
        """
        Gera e retorna um relatório em PDF para um checklist específico usando um template HTML.
        """
        try:
            checklist = self.get_object()
            
            # Prepara caminho do logo SIGA
            logo_siga_path = None
            logo_siga_static = settings.STATIC_ROOT / 'images' / 'logo-siga-gab.png'
            if logo_siga_static.exists():
                logo_siga_path = os.path.abspath(str(logo_siga_static)).replace('\\', '/')
            else:
                logo_siga_alt = settings.BASE_DIR / 'staticfiles' / 'images' / 'logo-siga-gab.png'
                if logo_siga_alt.exists():
                    logo_siga_path = os.path.abspath(str(logo_siga_alt)).replace('\\', '/')
                else:
                    logo_siga_path = request.build_absolute_uri('/static/images/logo-siga-gab.png')
            
            # Contexto para o template HTML
            context = {
                'checklist': checklist,
                'itens_status': checklist.itens_status.all().order_by('item_mestre__nome'),
                'data_emissao': timezone.now(),
                'logo_siga_path': logo_siga_path
            }

            # Renderiza o template HTML para uma string
            html_string = render_to_string('eventos/relatorio_checklist.html', context)
            
            # Gera o PDF a partir do HTML
            pdf_file = HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf()
            
            # Cria a resposta HTTP
            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="relatorio_checklist_{checklist.evento.nome}.pdf"'
            
            return response
        except EventoChecklist.DoesNotExist:
            return Response({'error': 'Checklist não encontrado.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='renovar-token')
    def renovar_token(self, request, pk=None):
        """
        Gera um novo token para o checklist, reseta seu status e o retorna.
        """
        try:
            checklist = self.get_object()
            
            # Gera um novo token UUID
            checklist.token = uuid.uuid4()
            
            # Reseta os campos relacionados ao preenchimento externo
            checklist.token_usado = False
            checklist.nome_responsavel = None
            checklist.data_envio = None
            
            checklist.save()
            
            # Retorna os dados atualizados do checklist
            serializer = self.get_serializer(checklist)
            return Response(serializer.data)
            
        except EventoChecklist.DoesNotExist:
            return Response({'error': 'Checklist não encontrado.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class PublicChecklistView(APIView):
    """
    View pública para o preenchimento do checklist via token.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, token, *args, **kwargs):
        try:
            checklist = EventoChecklist.objects.prefetch_related('itens_status__item_mestre').get(token=token, token_usado=False)
            serializer = EventoChecklistSerializer(checklist)
            return Response(serializer.data)
        except EventoChecklist.DoesNotExist:
            return Response({'error': 'Checklist inválido, expirado ou já preenchido.'}, status=status.HTTP_404_NOT_FOUND)

    def post(self, request, token, *args, **kwargs):
        try:
            checklist = EventoChecklist.objects.get(token=token)
        except EventoChecklist.DoesNotExist:
            return Response({'error': 'Checklist inválido ou expirado.'}, status=status.HTTP_400_BAD_REQUEST)

        items_data = request.data.get('items')
        nome_responsavel = request.data.get('nome_responsavel')

        if not nome_responsavel or items_data is None:
            return Response({'error': 'Dados incompletos.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            with transaction.atomic():
                # Apaga itens antigos para o caso de um reenvio do formulário
                EventoChecklistItemStatus.objects.filter(evento_checklist=checklist).delete()

                # Cria os novos itens com base no que foi selecionado no formulário
                for item_data in items_data:
                    master_id = item_data.get('master_id')
                    if not master_id:
                        continue
                    
                    EventoChecklistItemStatus.objects.create(
                        evento_checklist=checklist,
                        item_mestre_id=master_id,
                        observacoes=item_data.get('observacoes', ''),
                        concluido=False # O status 'concluido' foi removido da lógica
                    )
                
                # Atualiza o checklist principal
                checklist.token_usado = True
                checklist.nome_responsavel = nome_responsavel
                checklist.data_envio = timezone.now()
                checklist.save()

            return Response({'status': 'Checklist preenchido com sucesso!'})

        except Exception as e:
            return Response({'error': f'Ocorreu um erro interno: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ChecklistItemViewSet(viewsets.ModelViewSet):
    """
    API para listar e gerenciar (CRUD) os Itens Mestres de Checklist.
    """
    serializer_class = ChecklistItemSerializer
    permission_classes = [permissions.AllowAny]
    queryset = ChecklistItem.objects.all()

class EventoChecklistItemStatusViewSet(viewsets.ModelViewSet):
    """
    API para gerenciar os itens individuais (status) de um checklist de evento.
    """
    serializer_class = EventoChecklistItemStatusSerializer
    permission_classes = [PodeGerenciarEventos] # Reutiliza a permissão existente

    def get_queryset(self):
        # Garante que o usuário só possa ver/editar itens dos eventos de suas contas
        usuario = self.request.user
        if usuario.is_superuser:
            return EventoChecklistItemStatus.objects.all()
        if hasattr(usuario, 'perfil'):
            contas_do_usuario = usuario.perfil.contas.all()
            return EventoChecklistItemStatus.objects.filter(evento_checklist__evento__conta__in=contas_do_usuario)
        return EventoChecklistItemStatus.objects.none()

class MailingListViewSet(viewsets.ModelViewSet):
    """
    API para gerenciar Listas de Mailing.
    """
    serializer_class = MailingListSerializer
    permission_classes = [PodeGerenciarEventos]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return MailingList.objects.all().prefetch_related('municipes')
        if hasattr(user, 'perfil'):
            contas_do_usuario = user.perfil.contas.all()
            return MailingList.objects.filter(conta__in=contas_do_usuario).prefetch_related('municipes')
        return MailingList.objects.none()

    def perform_create(self, serializer):
        conta_do_usuario = self.request.user.perfil.contas.first()
        if conta_do_usuario:
            serializer.save(conta=conta_do_usuario)
        else:
            raise serializers.ValidationError("O usuário não está associado a nenhuma conta.")

    @action(detail=True, methods=['get'], url_path='municipes')
    def listar_municipes(self, request, pk=None):
        """
        Lista os munícipes que estão nesta lista de mailing.
        """
        mailing_list = self.get_object()
        municipes = mailing_list.municipes.all()
        page = self.paginate_queryset(municipes)
        if page is not None:
            serializer = MunicipeForConvidadoSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = MunicipeForConvidadoSerializer(municipes, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='add-municipe')
    def add_municipe(self, request, pk=None):
        """
        Adiciona um munícipe a uma lista de mailing, se ele tiver e-mail.
        """
        mailing_list = self.get_object()
        municipe_id = request.data.get('municipe_id')

        if not municipe_id:
            return Response({'error': 'O ID do munícipe é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            municipe = Municipe.objects.get(id=municipe_id, contas__in=[mailing_list.conta])
            
            # Restrição: verifica se o campo de e-mails não está vazio ou nulo
            if not municipe.emails or municipe.emails == '[]':
                 return Response({'error': 'O munícipe não possui e-mail cadastrado e não pode ser adicionado.'}, status=status.HTTP_400_BAD_REQUEST)

            mailing_list.municipes.add(municipe)
            return Response({'status': 'Munícipe adicionado com sucesso.'}, status=status.HTTP_200_OK)

        except Municipe.DoesNotExist:
            return Response({'error': 'Munícipe não encontrado ou não pertence à sua conta.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'], url_path='remove-municipe')
    def remove_municipe(self, request, pk=None):
        """
        Remove um munícipe de uma lista de mailing.
        """
        mailing_list = self.get_object()
        municipe_id = request.data.get('municipe_id')

        if not municipe_id:
            return Response({'error': 'O ID do munícipe é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            municipe = Municipe.objects.get(id=municipe_id)
            mailing_list.municipes.remove(municipe)
            return Response({'status': 'Munícipe removido com sucesso.'}, status=status.HTTP_200_OK)
        except Municipe.DoesNotExist:
             return Response({'error': 'Munícipe não encontrado.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'], url_path='add-by-category')
    def add_by_category(self, request, pk=None):
        """
        Adiciona todos os municipes de uma categoria que possuem email a esta lista de mailing.
        """
        mailing_list = self.get_object()
        categoria_id = request.data.get('categoria_id')

        if not categoria_id:
            return Response({'error': 'O ID da categoria é obrigatório.'}, status=status.HTTP_400_BAD_REQUEST)

        # Municipes que já estão na lista
        existing_municipes_ids = mailing_list.municipes.values_list('id', flat=True)

        # Encontra municipes com perfil na conta da mailing list e categoria selecionada
        municipes_to_add = Municipe.objects.filter(
            perfis__conta=mailing_list.conta,
            perfis__categoria_id=categoria_id
        ).exclude(
            id__in=existing_municipes_ids
        ).filter(
            Q(emails__isnull=False) & ~Q(emails__exact='[]')
        ).distinct()
        
        count = municipes_to_add.count()
        
        if count > 0:
            mailing_list.municipes.add(*municipes_to_add)

        return Response({'status': f'{count} novo(s) contato(s) com e-mail foram adicionados da categoria.'}, status=status.HTTP_200_OK)
 
class ExportMailingListCSVView(APIView):
    """
    Exporta os contatos (Nome e múltiplos Emails) de uma Mailing List
    para um arquivo .csv compatível com o Outlook.
    """
    permission_classes = [permissions.IsAuthenticated, PodeGerenciarEventos] 

    def get(self, request, pk, *args, **kwargs):
        try:
            mailing_list = MailingList.objects.get(pk=pk)
        except MailingList.DoesNotExist:
            return Response(
                {'error': 'Mailing list não encontrada.'}, 
                status=status.HTTP_404_NOT_FOUND
            )

        response = HttpResponse(
            content_type='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename="outlook_export_{mailing_list.nome.lower().replace(" ", "_")}.csv"'
            },
        )
        
        # BOM (Byte Order Mark) para UTF-8 (essencial para acentos)
        response.write('\ufeff')
        
        # --- MUDANÇAS APLICADAS AQUI ---
        # 1. Delimitador alterado para vírgula (,)
        # 2. Adicionado quoting=csv.QUOTE_ALL para forçar aspas em todos os campos
        writer = csv.writer(response, delimiter=',', quoting=csv.QUOTE_ALL) 
        
        # --- MUDANÇA NOS CABEÇALHOS ---
        MAX_EMAILS = 3 
        # 3. Cabeçalhos alterados para o padrão exato do Outlook
        headers = ['First Name', 'Last Name'] 
        for i in range(1, MAX_EMAILS + 1):
            if i == 1:
                headers.append('E-mail Address')
            else:
                headers.append(f'E-mail {i} Address')
        
        writer.writerow(headers)
        # --- FIM DAS MUDANÇAS ---

        municipes = mailing_list.municipes.all().order_by('nome_completo')
        
        for municipe in municipes:
            
            # --- LÓGICA PARA DIVIDIR O NOME ---
            nome_completo = municipe.nome_completo.strip()
            first_name = ""
            last_name = ""
            
            if " " in nome_completo:
                # Divide o nome apenas no primeiro espaço
                parts = nome_completo.split(" ", 1)
                first_name = parts[0]
                last_name = parts[1]
            else:
                # Se não tiver sobrenome, coloca tudo em "First Name"
                first_name = nome_completo
            
            row = [first_name, last_name]
            # --- FIM DA LÓGICA DO NOME ---
            
            # Lógica de extração de e-mails (continua a mesma)
            emails_do_contato = []
            if municipe.emails and isinstance(municipe.emails, list):
                for email_obj in municipe.emails:
                    if email_obj and isinstance(email_obj, dict) and email_obj.get('email'):
                        emails_do_contato.append(email_obj['email'])
            
            for i in range(MAX_EMAILS):
                if i < len(emails_do_contato):
                    row.append(emails_do_contato[i])
                else:
                    row.append('')

            writer.writerow(row)

        return response

class EventoAnalyticsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        evento_id = request.query_params.get('evento_id')
        data_inicio = request.query_params.get('data_inicio')
        data_fim = request.query_params.get('data_fim')
        categoria_id = request.query_params.get('categoria_id')
        
        user = request.user

        # --- FILTRO BASE DE SEGURANÇA ---
        eventos_qs = Evento.objects.all()
        if not user.is_superuser and hasattr(user, 'perfil'):
            eventos_qs = eventos_qs.filter(conta__in=user.perfil.contas.all())

        # --- FILTRO DE DATA ---
        current_tz = timezone.get_current_timezone()
        if data_inicio:
            try:
                dt_start = datetime.strptime(data_inicio, '%Y-%m-%d')
                start_aware = timezone.make_aware(datetime.combine(dt_start, time.min), current_tz)
                eventos_qs = eventos_qs.filter(data_evento__gte=start_aware)
            except ValueError: pass
        if data_fim:
            try:
                dt_end = datetime.strptime(data_fim, '%Y-%m-%d')
                end_aware = timezone.make_aware(datetime.combine(dt_end, time.max), current_tz)
                eventos_qs = eventos_qs.filter(data_evento__lte=end_aware)
            except ValueError: pass

        # =================================================================
        # MODO 1: DETALHE (UM EVENTO)
        # =================================================================
        if evento_id:
            try:
                evento = eventos_qs.get(id=evento_id)
            except Evento.DoesNotExist:
                return Response({'detail': 'Evento não encontrado.'}, status=404)

            # KPIs SIMPLIFICADOS
            # VIP: Conta registros na tabela Convidado (independente do status)
            total_convidados_vip = evento.convidados.count() 
            
            # Público: Conta registros na ListaPresenca (QR Code)
            total_publico_qr = evento.presentes.count()
            
            total_geral = total_convidados_vip + total_publico_qr

            # Perfil por categoria (perfis da conta do evento)
            ids_vips = set(evento.convidados.values_list('perfil__municipe_id', flat=True))
            ids_publico = set(evento.presentes.values_list('municipe_id', flat=True))
            todos_ids = (ids_vips | ids_publico) - {None}
            lista_perfil = []
            if todos_ids:
                por_categoria = (
                    PerfilMunicipe.objects.filter(conta=evento.conta, municipe_id__in=todos_ids)
                    .values('categoria__nome')
                    .annotate(qtd=Count('id'))
                    .order_by('-qtd')
                )
                lista_perfil = [{'municipe__categoria__nome': x['categoria__nome'], 'qtd': x['qtd']} for x in por_categoria]

            return Response({
                'tipo': 'detalhe',
                'evento': { 'nome': evento.nome, 'data': evento.data_evento, 'local': evento.local },
                'kpis': {
                    'vip': total_convidados_vip,
                    'publico': total_publico_qr,
                    'total': total_geral
                },
                'perfil': lista_perfil
            })

        # =================================================================
        # MODO 2: GLOBAL (MACRO)
        # =================================================================
        
        # 1. Total de Eventos no Período (NOVO)
        qtd_eventos = eventos_qs.count()

        # 2. QuerySets Base para Análise
        # VIP: Todos os registros de convidados (sem filtrar status='presente')
        all_convidados_qs = Convidado.objects.filter(evento__in=eventos_qs)
        
        # Público: Todos os registros de QR Code
        all_publico_qs = ListaPresenca.objects.filter(evento__in=eventos_qs)

        # Aplica Filtro de Categoria Global (se selecionado)
        if categoria_id:
            all_convidados_qs = all_convidados_qs.filter(perfil__categoria_id=categoria_id)
            all_publico_qs = all_publico_qs.filter(
                municipe__perfis__categoria_id=categoria_id,
                municipe__perfis__conta=F('evento__conta')
            ).distinct()

        # 3. Totais Gerais
        total_vip_geral = all_convidados_qs.count()
        total_publico_geral = all_publico_qs.count()

        # 4. Top 10 Eventos (Mais Movimentados: Soma de Convites VIP + QR Code)
        top_eventos_qs = eventos_qs.annotate(
            qtd_vip=Count('convidados', distinct=True), # Removemos o filtro de status aqui também
            qtd_publico=Count('presentes', distinct=True)
        ).annotate(
            total_real=F('qtd_vip') + F('qtd_publico')
        ).order_by('-total_real')[:10]

        top_eventos = []
        for ev in top_eventos_qs:
            top_eventos.append({
                'nome': ev.nome,
                'data': ev.data_evento,
                'total': ev.total_real
            })

        # 5. Top Convidados (VIP) - Mais Frequentes nas Listas
        top_convidados = (
            all_convidados_qs
            .values('perfil__municipe__nome_completo', 'perfil__categoria__nome')
            .annotate(frequencia=Count('id'))
            .order_by('-frequencia')[:10]
        )

        # 6. Top Público (QR Code) - categoria do perfil na conta do evento
        top_publico = (
            all_publico_qs.filter(municipe__perfis__conta=F('evento__conta'))
            .values('municipe__nome_completo', 'municipe__perfis__categoria__nome')
            .annotate(frequencia=Count('id'))
            .order_by('-frequencia')[:10]
        )

        # 7. Perfil Unificado
        perfis_vip = all_convidados_qs.values('perfil__categoria__nome').annotate(qtd=Count('id'))
        perfis_publico = all_publico_qs.filter(municipe__perfis__conta=F('evento__conta')).values(
            'municipe__perfis__categoria__nome'
        ).annotate(qtd=Count('id'))
        mapa_perfis = {}
        for item in perfis_vip:
            cat = item['perfil__categoria__nome'] or 'Sem Categoria'
            mapa_perfis[cat] = mapa_perfis.get(cat, 0) + item['qtd']
        for item in perfis_publico:
            cat = item['municipe__perfis__categoria__nome'] or 'Sem Categoria'
            mapa_perfis[cat] = mapa_perfis.get(cat, 0) + item['qtd']
        lista_top_perfis = [{'municipe__categoria__nome': k, 'qtd': v} for k, v in mapa_perfis.items()]
        lista_top_perfis = sorted(lista_top_perfis, key=lambda x: x['qtd'], reverse=True)[:5]

        return Response({
            'tipo': 'global',
            'qtd_eventos': qtd_eventos, # Contador de eventos
            'totais': {
                'publico': total_publico_geral,
                'vip': total_vip_geral,
                'geral': total_publico_geral + total_vip_geral
            },
            'top_eventos': top_eventos,
            'top_convidados': list(top_convidados),
            'top_publico': list(top_publico),
            'top_perfis': lista_top_perfis
        })
    
def get_bi_eventos_data(user, params):
    """
    Função helper que calcula os dados do BI.
    Usada tanto pela API JSON quanto pelo Gerador de PDF.
    """
    evento_id = params.get('evento_id')
    data_inicio = params.get('data_inicio')
    data_fim = params.get('data_fim')
    categoria_id = params.get('categoria_id')
    
    # --- FILTRO BASE DE SEGURANÇA ---
    eventos_qs = Evento.objects.all()
    if not user.is_superuser and hasattr(user, 'perfil'):
        eventos_qs = eventos_qs.filter(conta__in=user.perfil.contas.all())

    # --- FILTRO DE DATA ---
    current_tz = timezone.get_current_timezone()
    if data_inicio:
        try:
            dt_start = datetime.strptime(data_inicio, '%Y-%m-%d')
            start_aware = timezone.make_aware(datetime.combine(dt_start, time.min), current_tz)
            eventos_qs = eventos_qs.filter(data_evento__gte=start_aware)
        except ValueError: pass
    if data_fim:
        try:
            dt_end = datetime.strptime(data_fim, '%Y-%m-%d')
            end_aware = timezone.make_aware(datetime.combine(dt_end, time.max), current_tz)
            eventos_qs = eventos_qs.filter(data_evento__lte=end_aware)
        except ValueError: pass

    # =================================================================
    # MODO 1: DETALHE (UM EVENTO)
    # =================================================================
    if evento_id:
        try:
            evento = eventos_qs.get(id=evento_id)
        except Evento.DoesNotExist:
            return None # Sinal de erro

        total_convidados_vip = evento.convidados.count() 
        total_publico_qr = evento.presentes.count()
        total_geral = total_convidados_vip + total_publico_qr

        # Perfil por categoria (perfis da conta do evento)
        ids_vips = set(evento.convidados.values_list('perfil__municipe_id', flat=True))
        ids_publico = set(evento.presentes.values_list('municipe_id', flat=True))
        todos_ids = (ids_vips | ids_publico) - {None}
        lista_perfil = []
        if todos_ids:
            por_categoria = (
                PerfilMunicipe.objects.filter(conta=evento.conta, municipe_id__in=todos_ids)
                .values('categoria__nome')
                .annotate(qtd=Count('id'))
                .order_by('-qtd')
            )
            lista_perfil = [{'categoria': x['categoria__nome'], 'qtd': x['qtd']} for x in por_categoria]

        return {
            'tipo': 'detalhe',
            'evento': evento,
            'kpis': {
                'vip': total_convidados_vip,
                'publico': total_publico_qr,
                'total': total_geral
            },
            'perfil': lista_perfil
        }

    # =================================================================
    # MODO 2: GLOBAL (MACRO)
    # =================================================================
    qtd_eventos = eventos_qs.count()
    all_convidados_qs = Convidado.objects.filter(evento__in=eventos_qs)
    all_publico_qs = ListaPresenca.objects.filter(evento__in=eventos_qs)

    if categoria_id:
        all_convidados_qs = all_convidados_qs.filter(perfil__categoria_id=categoria_id)
        all_publico_qs = all_publico_qs.filter(
            municipe__perfis__categoria_id=categoria_id,
            municipe__perfis__conta=F('evento__conta')
        ).distinct()

    total_vip_geral = all_convidados_qs.count()
    total_publico_geral = all_publico_qs.count()

    # Top 10 Eventos
    top_eventos_qs = eventos_qs.annotate(
        qtd_vip=Count('convidados', distinct=True),
        qtd_publico=Count('presentes', distinct=True)
    ).annotate(
        total_real=F('qtd_vip') + F('qtd_publico')
    ).order_by('-total_real')[:10]

    # Top Convidados (VIP)
    top_convidados = (
        all_convidados_qs
        .values('perfil__municipe__nome_completo', 'perfil__categoria__nome')
        .annotate(frequencia=Count('id'))
        .order_by('-frequencia')[:10]
    )

    # Top Público (QR Code) - categoria do perfil na conta do evento
    top_publico = (
        all_publico_qs.filter(municipe__perfis__conta=F('evento__conta'))
        .values('municipe__nome_completo', 'municipe__perfis__categoria__nome')
        .annotate(frequencia=Count('id'))
        .order_by('-frequencia')[:10]
    )
    # Perfil Unificado Global
    perfis_vip = all_convidados_qs.values('perfil__categoria__nome').annotate(qtd=Count('id'))
    perfis_publico = all_publico_qs.filter(municipe__perfis__conta=F('evento__conta')).values(
        'municipe__perfis__categoria__nome'
    ).annotate(qtd=Count('id'))
    mapa_perfis = {}
    for item in perfis_vip:
        cat = item['perfil__categoria__nome'] or 'Sem Categoria'
        mapa_perfis[cat] = mapa_perfis.get(cat, 0) + item['qtd']
    for item in perfis_publico:
        cat = item['municipe__perfis__categoria__nome'] or 'Sem Categoria'
        mapa_perfis[cat] = mapa_perfis.get(cat, 0) + item['qtd']
    lista_top_perfis = [{'categoria': k, 'qtd': v} for k, v in mapa_perfis.items()]
    lista_top_perfis = sorted(lista_top_perfis, key=lambda x: x['qtd'], reverse=True)

    return {
        'tipo': 'global',
        'qtd_eventos': qtd_eventos,
        'totais': {
            'publico': total_publico_geral,
            'vip': total_vip_geral,
            'geral': total_publico_geral + total_vip_geral
        },
        'top_eventos': top_eventos_qs, # Passando QuerySet/Lista Objetos para facilitar template
        'top_convidados': top_convidados,
        'top_publico': top_publico,
        'top_perfis': lista_top_perfis
    }


# --- 2. VIEW DA API JSON (Refatorada para usar a função) ---
class EventoAnalyticsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        data = get_bi_eventos_data(request.user, request.query_params)
        if data is None:
            return Response({'detail': 'Evento não encontrado.'}, status=404)
        
        # Serialização manual rápida para JSON (pois o helper retorna objetos/querysets)
        if data['tipo'] == 'detalhe':
            return Response({
                'tipo': 'detalhe',
                'evento': { 'nome': data['evento'].nome, 'data': data['evento'].data_evento, 'local': data['evento'].local },
                'kpis': data['kpis'],
                'perfil': [{'municipe__categoria__nome': p['categoria'], 'qtd': p['qtd']} for p in data['perfil']]
            })
        else:
            # Serializa listas globais
            top_eventos = [{'nome': ev.nome, 'data': ev.data_evento, 'total': ev.total_real} for ev in data['top_eventos']]
            return Response({
                'tipo': 'global',
                'qtd_eventos': data['qtd_eventos'],
                'totais': data['totais'],
                'top_eventos': top_eventos,
                'top_convidados': list(data['top_convidados']),
                'top_publico': list(data['top_publico']),
                'top_perfis': [{'municipe__categoria__nome': p['categoria'], 'qtd': p['qtd']} for p in data['top_perfis']]
            })


# --- 3. NOVA VIEW DO PDF ---
class GerarPdfBiEventosView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        # 1. Obtém os mesmos dados da API
        data_bi = get_bi_eventos_data(request.user, request.query_params)
        
        if data_bi is None:
            return HttpResponse('Evento não encontrado', status=404)
        
        # 1. Tenta recuperar a conta de forma segura
        conta = None
        if hasattr(request.user, 'perfil') and request.user.perfil.contas.exists():
            conta = request.user.perfil.contas.first()

        # 2. Define valores PADRÃO (Fallback) caso não tenha conta ou brasão
        nome_instituicao = "Prefeitura Municipal de Mogi das Cruzes"
        
        # Caminho do brasão padrão nos arquivos estáticos
        caminho_brasao = os.path.join(settings.STATIC_ROOT, 'images', 'logo-brasao-prefeitura.png')
        
        # 3. Se tiver conta, tenta sobrescrever com os dados personalizados
        if conta:
            nome_instituicao = conta.nome_instituicao or nome_instituicao
            
            # Só tenta acessar o brasão SE ele existir
            if conta.brasao_instituicao:
                try:
                    # Pega o caminho físico
                    if os.path.exists(conta.brasao_instituicao.path):
                        caminho_brasao = conta.brasao_instituicao.path
                except Exception:
                    pass # Se der erro (arquivo não encontrado), mantém o padrão

        # 4. Prepara a URI para o PDF (file://)
        if os.path.exists(caminho_brasao):
            logo_uri = f"file://{caminho_brasao}"
        else:
            logo_uri = "" # Se não achar nem o padrão, vai sem logo

        # 2. Prepara contexto para o HTML
        context = {
            'data': data_bi,
            'filtros': {
                'inicio': request.query_params.get('data_inicio'),
                'fim': request.query_params.get('data_fim'),
                'categoria': request.query_params.get('categoria_nome') # Frontend pode mandar o nome para exibir bonito
            },
            'user': request.user,
            'data_geracao': datetime.now(),
            'brasao_url': logo_uri,
        }

        # 3. Renderiza HTML
        html_string = render_to_string('relatorios/bi_eventos_report.html', context)
        
        # 4. Gera PDF com WeasyPrint
        pdf_file = weasyprint.HTML(string=html_string).write_pdf()
        
        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="relatorio_eventos.pdf"'
        return response