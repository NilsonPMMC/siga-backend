# eventos/views.py
import datetime
from openpyxl import Workbook
from django.http import HttpResponse
from django.db.models import Q 
from django.shortcuts import render, get_object_or_404, redirect
from django.db import transaction
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework import viewsets, permissions, serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Evento, ListaPresenca, EventoChecklist, Convidado, Comunicacao, Destinatario, LogDeEnvio 
from atendimentos.models import Municipe, CategoriaContato 
from .forms import ListaPresencaForm
from .serializers import EventoSerializer, ConvidadoSerializer, ComunicacaoSerializer, DestinatarioSerializer, LogDeEnvioSerializer, ListaPresencaSerializer, EventoChecklistSerializer 
from .utils import gerar_e_enviar_certificado
from .permissions import PodeGerenciarEventos
from eventos.tasks import enviar_comunicacao_em_massa, gerar_e_enviar_certificado

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
            convidados_existentes_ids = Convidado.objects.filter(evento=evento).values_list('municipe_id', flat=True)

            # --- CORREÇÃO FINAL E EXPLÍCITA ---
            # Usamos 'categoria__id' que é a forma padrão do Django para buscar em chaves estrangeiras.
            # O traceback nos confirmou que o campo se chama 'categoria'.
            municipes_para_adicionar = Municipe.objects.filter(
                categoria__id=categoria_id
            ).exclude(
                id__in=convidados_existentes_ids
            )
            # ------------------------------------

            print(f"Encontrados {municipes_para_adicionar.count()} novos munícipes para adicionar.")

            novos_convidados = [
                Convidado(evento=evento, municipe=municipe)
                for municipe in municipes_para_adicionar
            ]
            
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

            # 2. Encontra os munícipes da categoria que ainda não foram adicionados.
            municipes_para_adicionar = Municipe.objects.filter(
                categoria__id=categoria_id
            ).exclude(
                id__in=destinatarios_existentes_ids
            ).filter(
                Q(emails__isnull=False) & ~Q(emails__exact='[]')
            )

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
    permission_classes = [PodeGerenciarEventos] # Usa a mesma permissão dos eventos

    def get_queryset(self):
        usuario = self.request.user
        qs = Convidado.objects.none()

        if usuario.is_superuser:
            qs = Convidado.objects.all()
        elif hasattr(usuario, 'perfil'):
            contas_do_usuario = usuario.perfil.contas.all()
            qs = Convidado.objects.filter(evento__conta__in=contas_do_usuario)
        
        evento_id = self.request.query_params.get('evento')
        if evento_id:
            return qs.filter(evento_id=evento_id)
        return qs.select_related('municipe')

# ... (imports e outras ViewSets) ...

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

        # 2. Encontra os munícipes da categoria que AINDA NÃO estão na lista e TÊM E-MAIL.
        municipes_para_adicionar = Municipe.objects.filter(
            categoria__id=categoria_id
        ).exclude(
            id__in=destinatarios_existentes_ids
        ).filter(
            Q(emails__isnull=False) & ~Q(emails__exact='[]')
        )

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
    permission_classes = [permissions.AllowAny] # Permite acesso anônimo

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
                'logo_url': logo_url,
                'brasao_url': brasao_url,
            })
        except Evento.DoesNotExist:
            return Response({'error': 'Nenhum evento ativo encontrado para esta conta.'}, status=status.HTTP_404_NOT_FOUND)

    def post(self, request, conta_id, *args, **kwargs):
        """
        Processa o formulário de check-in.
        """
        try:
            evento_ativo = Evento.objects.get(conta_id=conta_id, ativo=True)
            conta = evento_ativo.conta
        except Evento.DoesNotExist:
            return Response({'error': 'Nenhum evento ativo para fazer check-in.'}, status=status.HTTP_400_BAD_REQUEST)

        # Dados do formulário
        nome_completo = request.data.get('nome_completo')
        telefone = request.data.get('telefone')
        email = request.data.get('email')
        data_nascimento_str = request.data.get('data_nascimento') 
        orgao = request.data.get('orgao')
        
        if not nome_completo or not telefone:
             return Response({'error': 'Nome e telefone são obrigatórios.'}, status=status.HTTP_400_BAD_REQUEST)

        municipe = Municipe.objects.filter(
            nome_completo=nome_completo,
            contas=conta
        ).first()

        municipe_existente = Municipe.objects.filter(nome_completo=nome_completo, contas=conta).first()

        # 2. Prepara o dicionário de dados para atualizar ou criar o munícipe
        defaults = {
            'telefones': [{'tipo': 'principal', 'numero': telefone}],
            'emails': [{'tipo': 'principal', 'email': email}] if email else None,
            'orgao': orgao,
        }
        if data_nascimento_str and '/' in data_nascimento_str:
            try:
                dia, mes = data_nascimento_str.split('/')
                data_valida = datetime.date(2000, int(mes), int(dia))
                defaults['data_nascimento'] = data_valida.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                pass

        # Lógica para encontrar ou criar o munícipe (agora com os novos campos)
        if municipe_existente:
            for key, value in defaults.items():
                setattr(municipe_existente, key, value)
            municipe_existente.save()
            municipe = municipe_existente
        else:
            categoria_municipe, _ = CategoriaContato.objects.get_or_create(nome="Munícipe")
            defaults['categoria'] = categoria_municipe
            
            municipe = Municipe.objects.create(
                nome_completo=nome_completo,
                **defaults
            )
            municipe.contas.add(conta)

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
            return Response({'status': 'Sua presença neste evento já foi registrada.'})

        if presenca.email:
            gerar_e_enviar_certificado.delay(presenca.id)

        return Response({'status': 'Presença registrada com sucesso!'})

class ListaPresencaViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API para visualizar a Lista de Presença de um evento.
    """
    serializer_class = ListaPresencaSerializer
    permission_classes = [PodeGerenciarEventos]
    filterset_fields = ['evento']

    def get_queryset(self):
        # Lógica de permissão que já usamos, garantindo segurança
        usuario = self.request.user
        if usuario.is_superuser:
            return ListaPresenca.objects.all().order_by('-data_registro')
        if hasattr(usuario, 'perfil'):
            contas_do_usuario = usuario.perfil.contas.all()
            return ListaPresenca.objects.filter(evento__conta__in=contas_do_usuario)
        return ListaPresenca.objects.none()

    @action(detail=False, methods=['get'], url_path='exportar-excel')
    def exportar_excel(self, request):
        evento_id = request.query_params.get('evento')
        if not evento_id:
            return Response({"error": "O ID do evento é obrigatório."}, status=status.HTTP_400_BAD_REQUEST)

        # O queryset base já é filtrado por permissão do usuário
        queryset = self.get_queryset().filter(evento_id=evento_id)
        
        # A variável 'Workbook' agora está definida por causa do import
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
    queryset = EventoChecklist.objects.all()

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
            checklist = EventoChecklist.objects.get(token=token, token_usado=False)
        except EventoChecklist.DoesNotExist:
            return Response({'error': 'Checklist inválido ou já preenchido.'}, status=status.HTTP_400_BAD_REQUEST)

        # 'items' será uma lista de objetos: [{id: 1, concluido: true, observacoes: '...'}, ...]
        items_data = request.data.get('items')
        
        # Atualiza cada item do checklist
        for item_data in items_data:
            EventoChecklistItemStatus.objects.filter(id=item_data.get('id'), evento_checklist=checklist).update(
                concluido=item_data.get('concluido', False),
                observacoes=item_data.get('observacoes', '')
            )
        
        # Marca o token como usado e salva o nome do responsável
        checklist.token_usado = True
        checklist.nome_responsavel = request.data.get('nome_responsavel', 'Anônimo')
        checklist.data_envio = timezone.now()
        checklist.save()

        return Response({'status': 'Checklist preenchido com sucesso!'})