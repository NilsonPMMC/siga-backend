# etiquetas/views.py
import csv
import io
import re

from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.template import Context, Template
from django.http import HttpResponse
from django.db import transaction

from .models import EtiquetaTemplate, GeracaoEtiqueta
from atendimentos.models import Municipe
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


class ImportarDadosEtiquetaCSVAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        def _norm_header(texto):
            base = str(texto or "").strip().lower()
            base = base.replace("\ufeff", "")
            base = re.sub(r"\s+", " ", base)
            base = base.replace("_", " ")
            base = re.sub(r"[^a-z0-9 ]+", "", base)
            return base.strip()

        dry_run_raw = str(request.data.get("dry_run", "")).strip().lower()
        dry_run = dry_run_raw in {"1", "true", "yes", "sim", "on"}
        arquivo = request.FILES.get("arquivo")
        if not arquivo:
            return Response(
                {"detail": "Arquivo CSV é obrigatório no campo 'arquivo'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw = arquivo.read()
        if not raw:
            return Response({"detail": "Arquivo CSV vazio."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = raw.decode("latin-1")

        linhas_csv = content.splitlines()
        while linhas_csv and not linhas_csv[0].strip():
            linhas_csv.pop(0)
        if linhas_csv and linhas_csv[0].strip().lower().startswith("sep="):
            linhas_csv.pop(0)
        content = "\n".join(linhas_csv)

        primeira_linha = content.splitlines()[0] if content else ""
        if ";" in primeira_linha and "," in primeira_linha:
            delimitador = ";" if primeira_linha.count(";") >= primeira_linha.count(",") else ","
        elif ";" in primeira_linha:
            delimitador = ";"
        elif "," in primeira_linha:
            delimitador = ","
        else:
            amostra = content[:2048]
            try:
                dialect = csv.Sniffer().sniff(amostra, delimiters=";,")
                delimitador = dialect.delimiter
            except csv.Error:
                delimitador = ";"

        reader = csv.DictReader(io.StringIO(content), delimiter=delimitador)
        if not reader.fieldnames:
            return Response({"detail": "Cabeçalho CSV inválido."}, status=status.HTTP_400_BAD_REQUEST)

        headers = {_norm_header(h): h for h in reader.fieldnames if h}
        id_col = headers.get("id")
        etiqueta_col = (
            headers.get("dados de etiqueta")
            or headers.get("dados etiqueta")
            or headers.get("dadosetiqueta")
        )
        if (not id_col or not etiqueta_col) and len(reader.fieldnames) == 1:
            unico = (reader.fieldnames[0] or "").strip()
            if ";" in unico:
                reader = csv.DictReader(io.StringIO(content), delimiter=";")
                headers = {_norm_header(h): h for h in (reader.fieldnames or []) if h}
                id_col = headers.get("id")
                etiqueta_col = (
                    headers.get("dados de etiqueta")
                    or headers.get("dados etiqueta")
                    or headers.get("dadosetiqueta")
                )
            elif "," in unico:
                reader = csv.DictReader(io.StringIO(content), delimiter=",")
                headers = {_norm_header(h): h for h in (reader.fieldnames or []) if h}
                id_col = headers.get("id")
                etiqueta_col = (
                    headers.get("dados de etiqueta")
                    or headers.get("dados etiqueta")
                    or headers.get("dadosetiqueta")
                )
        if not id_col or not etiqueta_col:
            cols = [str(h) for h in (reader.fieldnames or [])]
            return Response(
                {
                    "detail": "CSV deve conter colunas 'id' e 'dados de etiqueta' (ou 'dados_etiqueta').",
                    "colunas_recebidas": cols,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request.user.is_superuser:
            allowed_qs = Municipe.objects.all()
        elif hasattr(request.user, "perfil"):
            allowed_qs = Municipe.objects.filter(contas__in=request.user.perfil.contas.all()).distinct()
        else:
            allowed_qs = Municipe.objects.none()

        allowed_map = {m.id: m for m in allowed_qs.only("id", "dados_etiqueta")}

        atualizados = []
        atualizados_preview = []
        linhas_invalidas = []
        ids_nao_encontrados = []
        total_linhas = 0

        for idx, row in enumerate(reader, start=2):
            total_linhas += 1
            id_raw = (row.get(id_col) or "").strip()
            if not id_raw:
                linhas_invalidas.append({"linha": idx, "erro": "id vazio"})
                continue
            try:
                mun_id = int(id_raw)
            except ValueError:
                linhas_invalidas.append({"linha": idx, "erro": f"id inválido: {id_raw}"})
                continue

            mun = allowed_map.get(mun_id)
            if not mun:
                ids_nao_encontrados.append(mun_id)
                continue

            novo_texto = (row.get(etiqueta_col) or "").strip()
            if (mun.dados_etiqueta or "").strip() == novo_texto:
                continue
            if len(atualizados_preview) < 20:
                atualizados_preview.append(
                    {
                        "id": mun.id,
                        "antes": (mun.dados_etiqueta or "").strip(),
                        "depois": novo_texto,
                    }
                )
            mun.dados_etiqueta = novo_texto
            atualizados.append(mun)

        if not dry_run:
            with transaction.atomic():
                if atualizados:
                    Municipe.objects.bulk_update(atualizados, ["dados_etiqueta"], batch_size=500)

        return Response(
            {
                "ok": True,
                "dry_run": dry_run,
                "total_linhas": total_linhas,
                "atualizados": len(atualizados),
                "atualizados_preview": atualizados_preview,
                "ids_nao_encontrados": sorted(set(ids_nao_encontrados)),
                "linhas_invalidas": linhas_invalidas[:100],
                "linhas_invalidas_total": len(linhas_invalidas),
            },
            status=status.HTTP_200_OK,
        )