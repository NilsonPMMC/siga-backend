# etiquetas/views.py
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.template import Context, Template
from django.http import HttpResponse

from .models import EtiquetaTemplate, GeracaoEtiqueta
from .serializers import (
    EtiquetaTemplateSerializer, 
    GeracaoEtiquetaSerializer, 
    GerarEtiquetaRequestSerializer
)

class EtiquetaTemplateViewSet(viewsets.ModelViewSet):
    """
    API endpoint que permite aos usuários visualizar e editar templates de etiqueta.
    Fornece as ações de list, create, retrieve, update e destroy.
    """
    queryset = EtiquetaTemplate.objects.all()
    serializer_class = EtiquetaTemplateSerializer
    permission_classes = [IsAuthenticated]

class GerarEtiquetaAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        request_serializer = GerarEtiquetaRequestSerializer(data=request.data)
        if not request_serializer.is_valid():
            return Response(request_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = request_serializer.validated_data
        template_id = validated_data.get('template_id')
        contatos_data = validated_data.get('contatos')
        posicao_inicial = validated_data.get('posicao_inicial', 1)
        imprimir_remetente = request.data.get('imprimir_remetente', False)

        template_obj = get_object_or_404(EtiquetaTemplate, pk=template_id)
        
        texto_remetente = ""
        if imprimir_remetente:
            if hasattr(request.user, 'perfil') and request.user.perfil.contas.exists():
                conta = request.user.perfil.contas.first()
                texto_remetente = conta.etiqueta_remetente if conta.etiqueta_remetente else ""
                
        ITENS_POR_PAGINA = template_obj.etiquetas_por_pagina
        
        try:
            pos_inicial_int = int(posicao_inicial) if posicao_inicial else 1
            if pos_inicial_int < 1: pos_inicial_int = 1
        except (ValueError, TypeError):
            pos_inicial_int = 1
        
        items_iniciais_vazios = [None] * (pos_inicial_int - 1)
        lista_completa_itens = items_iniciais_vazios + contatos_data
        
        paginas = []
        if lista_completa_itens:
            for i in range(0, len(lista_completa_itens), ITENS_POR_PAGINA):
                pagina = lista_completa_itens[i:i + ITENS_POR_PAGINA]
                paginas.append(pagina)

        template = Template(template_obj.template_html)
        context = Context({
            'paginas': paginas,
            'remetente': texto_remetente,
            'flag_imprimir_remetente': imprimir_remetente
        })
        html_renderizado = template.render(context)
        
        return HttpResponse(html_renderizado, content_type='text/html; charset=utf-8')