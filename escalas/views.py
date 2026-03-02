import os
from rest_framework import viewsets, permissions, status, generics, serializers
from rest_framework.response import Response
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from weasyprint import HTML, CSS
from django.db.models import Q
from atendimentos.models import Municipe
from .models import EscalaPeriodo, EscalaRegistro, ContatoEmergencia
from .serializers import EscalaPeriodoSerializer, EscalaRegistroSerializer, ContatoEmergenciaSerializer

class EscalaPeriodoViewSet(viewsets.ModelViewSet):
    """
    Períodos de Escala.
    Leitura: Todos logados.
    Escrita: Apenas Admin/Gabinete.
    """
    queryset = EscalaPeriodo.objects.all().order_by('-data_inicio')
    serializer_class = EscalaPeriodoSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        # Se for criar/editar/excluir, precisa ser Admin ou Grupo Gabinete
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            # Implementar lógica IsAdminOrGabinete aqui se quiser restringir
            pass 
        return super().get_permissions()


class EscalaRegistroViewSet(viewsets.ModelViewSet):
    """
    Registros de Plantonistas.
    """
    serializer_class = EscalaRegistroSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = EscalaRegistro.objects.none() # Começa vazio por segurança

        # --- 1. DEFINIÇÃO DO UNIVERSO PERMITIDO (Permissões) ---
        
        # Visão do Gestor (Vê tudo)
        if user.is_superuser or user.groups.filter(name='Gestor de Escalas').exists():
            queryset = EscalaRegistro.objects.all().select_related('conta', 'servidor', 'periodo')
        
        # Visão da Secretaria (Vê só o seu)
        elif user.groups.filter(name='Escalas').exists() and hasattr(user, 'perfil'):
            contas_usuario = user.perfil.contas.all()
            queryset = EscalaRegistro.objects.filter(conta__in=contas_usuario).select_related('conta', 'servidor', 'periodo')
        
        # Se não caiu em nenhum if acima, retorna vazio
        else:
            return EscalaRegistro.objects.none()

        # --- 2. APLICAÇÃO DE FILTROS DA URL (A CORREÇÃO) ---
        
        # Filtra pelo Período (ex: ?periodo=3)
        periodo_id = self.request.query_params.get('periodo')
        if periodo_id:
            queryset = queryset.filter(periodo_id=periodo_id)

        # Filtra pela Conta (ex: ?conta=5) - Opcional, mas útil
        conta_id = self.request.query_params.get('conta')
        if conta_id:
            queryset = queryset.filter(conta_id=conta_id)

        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        
        # 1. Se for Gestor ou Admin, salva direto (eles têm permissão total)
        if user.is_superuser or user.groups.filter(name='Gestor de Escalas').exists():
            serializer.save(registrado_por=user)
            return

        # 2. Se for usuário comum (Secretaria), precisamos validar a conta
        # AQUI ESTAVA O ERRO: Precisamos definir 'conta_alvo' pegando dos dados validados
        conta_alvo = serializer.validated_data['conta']
        
        # Verifica se a conta do payload pertence ao perfil do usuário
        if hasattr(user, 'perfil') and conta_alvo in user.perfil.contas.all():
            serializer.save(registrado_por=user)
        else:
            raise permissions.PermissionDenied("Você não tem permissão para escalar plantonistas para esta secretaria.")
        
class ContatoEmergenciaViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Lista simples de contatos de emergência (Apenas Leitura para a API)
    """
    queryset = ContatoEmergencia.objects.filter(ativo=True).order_by('ordem', 'nome')
    serializer_class = ContatoEmergenciaSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None # Não paginar, queremos ver todos de uma vez

def relatorio_escala_pdf(request, periodo_id):
    # 1. Busca os dados
    periodo = get_object_or_404(EscalaPeriodo, pk=periodo_id)
    
    # Busca todas as contas que participam da escala (para mostrar mesmo as pendentes, se quiser)
    # Ou busca apenas os registros feitos. O modelo sugere listar quem está escalado.
    # Vamos buscar os registros ordenados por Secretaria
    registros = EscalaRegistro.objects.filter(
        periodo=periodo
    ).select_related('conta', 'servidor').order_by('conta__nome', 'servidor__nome_completo')
    
    # Busca telefones de emergência ativos
    emergencias = ContatoEmergencia.objects.filter(ativo=True).order_by('ordem')

    caminho_logo = os.path.join(settings.STATIC_ROOT, 'images', 'logo-brasao-prefeitura.png')
    
    # Se quiser garantir, converta para URI de arquivo
    if os.path.exists(caminho_logo):
        logo_uri = f"file://{caminho_logo}"
    else:
        # Fallback caso não ache (opcional)
        logo_uri = ""

    # 2. Contexto para o Template
    context = {
        'periodo': periodo,
        'registros': registros,
        'emergencias': emergencias,
        'data_geracao': timezone.now(),
        'logo_uri': logo_uri,
    }

    # 3. Renderiza HTML
    html_string = render_to_string('escalas/relatorio_escala.html', context)

    # 4. Converte para PDF
    # base_url é necessário para carregar CSS e Imagens locais
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    
    # CSS específico para impressão (margens, tamanho A4)
    css = CSS(string='''
        @page { size: A4; margin: 1.5cm; }
        body { font-family: sans-serif; }
    ''')
    
    pdf_file = html.write_pdf(stylesheets=[css])

    # 5. Retorna o arquivo
    response = HttpResponse(pdf_file, content_type='application/pdf')
    # 'inline' abre no navegador, 'attachment' baixa direto
    filename = f"escala_{periodo.data_inicio.strftime('%Y%m%d')}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    
    return response

# Serializer simples e leve (JSON puro, sem riscos)
class ServidorSimplesSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()
    
    class Meta:
        model = Municipe
        fields = ['id', 'nome_completo', 'cargo', 'telefones', 'label']

    def get_label(self, obj):
        return f"{obj.nome_completo} ({obj.cargo or 'Servidor'})"

class ServidorLookupView(generics.ListAPIView):
    serializer_class = ServidorSimplesSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        # Busca Global de Servidores (Diretório Corporativo)
        # Filtra apenas quem tem CARGO ou está na categoria SERVIDOR
        termo = self.request.query_params.get('q', self.request.query_params.get('params[q]', ''))
        
        categorias_servidores = ['SERVIDOR(A)', 'SECRETÁRIO(A) MUNICIPAL', 'SERVIDOR(A) SEMAE']
        
        queryset = Municipe.objects.filter(
            Q(perfis__categoria__nome__in=categorias_servidores) | 
            Q(cargo__isnull=False)
        ).exclude(cargo='').distinct()

        if termo:
            queryset = queryset.filter(nome_completo__icontains=termo)

        return queryset.order_by('nome_completo')[:20]