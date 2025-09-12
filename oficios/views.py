from rest_framework import viewsets, permissions
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from django.http import HttpResponse
from django.template.loader import render_to_string
import traceback
import os # Adicionar este import
import google.generativeai as genai
from django.conf import settings # Adicionar este import

from .models import Oficio
from .serializers import OficioSerializer
from .permissions import CanManageOficiosPermission

try:
    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash') # Modelo rápido e eficiente
except Exception as e:
    print(f"ERRO ao configurar a API do Gemini: {e}")
    model = None

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

class GerarTextoOficioIAView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanManageOficiosPermission]

    def post(self, request, *args, **kwargs):
        if not model:
            return Response(
                {"detail": "O serviço de IA não está configurado corretamente."},
                status=503 # Service Unavailable
            )

        diretrizes = request.data.get('diretrizes', '')
        texto_existente = request.data.get('texto_existente', '')
        
        if not diretrizes:
            return Response({"detail": "As diretrizes são necessárias."}, status=400)

        # Monta o prompt detalhado para a IA
        prompt = f"""
        Você é um assistente de redação especialista em correspondência oficial para a Prefeitura de Mogi das Cruzes.
        Sua tarefa é redigir ou aprimorar o corpo de um ofício com base nas diretrizes fornecidas.
        O texto deve ser formal, claro, conciso, respeitoso e seguir a norma culta da língua portuguesa.

        **Diretrizes do usuário:** "{diretrizes}"
        """

        if texto_existente:
            prompt += f'\n\n**Texto atual para aprimorar:** "{texto_existente}"'
            prompt += "\n\n**Tarefa:** Com base nas diretrizes, revise e melhore o texto atual. Não inclua saudações como 'Senhor Prefeito' ou despedidas como 'Atenciosamente', foque apenas no corpo do ofício."
        else:
            prompt += "\n\n**Tarefa:** Com base nas diretrizes, crie um rascunho para o corpo do ofício. Não inclua saudações ou despedidas, apenas o texto principal."

        try:
            response = model.generate_content(prompt)
            # Retorna o texto gerado pela IA para o frontend
            return Response({'texto_gerado': response.text})
        except Exception as e:
            print(f"Erro na API do Gemini: {e}")
            return Response({"detail": f"Ocorreu um erro ao comunicar com o serviço de IA: {e}"}, status=500)