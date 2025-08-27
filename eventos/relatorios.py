# (dentro de eventos/relatorios.py)
import io
from datetime import datetime
from django.utils import timezone
from django.conf import settings
import os
from django.http import HttpResponse
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.units import inch
from reportlab.lib import colors

def gerar_pdf_checklist(checklist):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    
    styles = getSampleStyleSheet()
    
    evento = checklist.evento
    elements = []
    
    # --- Cabeçalho ---
    elements.append(Paragraph("Relatório de Checklist do Evento", styles['h1']))
    elements.append(Spacer(1, 0.2*inch))
    
    # --- Informações do Evento ---
    elements.append(Paragraph(evento.nome, styles['h2']))
    
    data_formatada = evento.data_evento.strftime('%d/%m/%Y às %H:%M')
    info_evento_texto = f"<b>Data:</b> {data_formatada} | <b>Local:</b> {evento.local}"
    elements.append(Paragraph(info_evento_texto, styles['Normal']))
    elements.append(Spacer(1, 0.2*inch))
    
    # --- CORREÇÃO APLICADA AQUI ---
    # Verifica se o checklist foi preenchido antes de tentar mostrar os dados do responsável.
    if checklist.token_usado and checklist.nome_responsavel and checklist.data_envio:
        data_envio_formatada = checklist.data_envio.strftime('%d/%m/%Y')
        info_preenchimento = f"<b>Preenchido por:</b> {checklist.nome_responsavel} em {data_envio_formatada}"
        elements.append(Paragraph(info_preenchimento, styles['Normal']))
    else:
        # Se não foi preenchido, exibe uma mensagem de status no lugar.
        elements.append(Paragraph("<b>Status:</b> Checklist ainda não preenchido externamente.", styles['Normal']))

    elements.append(Spacer(1, 0.3*inch))
    
    # --- Itens Selecionados no Checklist ---
    elements.append(Paragraph("Itens Selecionados no Checklist", styles['h3']))
    
    itens_status = checklist.itens_status.all().select_related('item_mestre').order_by('item_mestre__nome')
    
    if not itens_status:
        elements.append(Paragraph("Nenhum item foi selecionado para este checklist.", styles['Italic']))
    else:
        for item in itens_status:
            item_texto = f"<b>✓ {item.item_mestre.nome}</b>"
            elements.append(Paragraph(item_texto, styles['Normal']))
            
            # Garante que as observações sejam tratadas como string para evitar erros
            observacoes = str(item.observacoes) if item.observacoes else ''
            
            if observacoes:
                obs_texto = f"<para leftIndent=20>{observacoes}</para>"
                elements.append(Paragraph(obs_texto, styles['Italic']))
            else:
                obs_texto = "<para leftIndent=20><i>(Sem informações adicionais)</i></para>"
                elements.append(Paragraph(obs_texto, styles['Italic']))
            elements.append(Spacer(1, 0.1*inch))
            
    doc.build(elements)
    
    buffer.seek(0)
    return buffer

def gerar_pdf_eventos_periodo(eventos, data_inicio, data_fim, logo_path):
    buffer = io.BytesIO()
    
    # --- AJUSTE 1: Mudar para paisagem ---
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), rightMargin=inch/2, leftMargin=inch/2, topMargin=inch/2, bottomMargin=inch/2)
    
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Right', alignment=TA_RIGHT))
    
    elements = []
    
    # --- AJUSTE 2: Incluir o logo do SIGA no cabeçalho ---
    header_data = []
    if logo_path and os.path.exists(logo_path):
        logo = Image(logo_path, width=1*inch, height=1*inch)
        logo.hAlign = 'LEFT'
        
        titulo_texto = "Relatório de Eventos"
        periodo_texto = f"Período de {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
        
        # Cria um parágrafo com múltiplas linhas para o título e período
        texto_cabecalho = f"<font size='18'>{titulo_texto}</font><br/><font size='12'>{periodo_texto}</font>"
        p_cabecalho = Paragraph(texto_cabecalho, styles['h1'])
        p_cabecalho.style.alignment = TA_CENTER
        
        # Usa uma tabela para alinhar o logo e o texto
        header_data = [[logo, p_cabecalho]]
        header_table = Table(header_data, colWidths=[1.2*inch, 9*inch])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ]))
        elements.append(header_table)
    else:
        # Fallback se o logo não for encontrado
        elements.append(Paragraph("Relatório de Eventos", styles['h1']))
        periodo_str = f"Período de {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
        elements.append(Paragraph(periodo_str, styles['Normal']))

    elements.append(Spacer(1, 0.3*inch))
    
    # --- Tabela de Eventos ---
    if not eventos:
        elements.append(Paragraph("Nenhum evento encontrado para o período selecionado.", styles['Normal']))
    else:
        dados_tabela = [["Data", "Hora", "Evento", "Local", "Status"]]
        estilos_linhas = []
        hoje = timezone.now().date()

        for i, evento in enumerate(eventos, start=1):
            dados_tabela.append([
                evento.data_evento.strftime('%d/%m/%Y'),
                evento.data_evento.strftime('%H:%M'),
                Paragraph(evento.nome, styles['Normal']),
                Paragraph(evento.local, styles['Normal']),
                evento.get_status_display()
            ])
            
            # --- AJUSTE 3: Lógica de cores das linhas ---
            # Regra 1: Eventos passados
            if evento.data_evento.date() < hoje:
                if evento.status == 'cancelado':
                    estilos_linhas.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#FFCDD2'))) # Vermelho claro
                else:
                    estilos_linhas.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#E0E0E0'))) # Cinza
            # Regra 2: Eventos futuros ou de hoje
            else:
                if evento.status == 'cancelado':
                    estilos_linhas.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#FFCDD2'))) # Vermelho claro
                else:
                    estilos_linhas.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#fff')))
                # Se for 'agendado' ou outro, não adiciona cor (fundo branco/bege padrão)

        table = Table(dados_tabela, colWidths=[1.2*inch, 0.8*inch, 3.5*inch, 3.5*inch, 1*inch])
        
        style = TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4A90E2')),
            ('TEXTCOLOR',(0,0),(-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#F7F7F7')), # Fundo padrão mais claro
            ('GRID', (0,0), (-1,-1), 1, colors.black),
        ])
        
        # Adiciona os estilos de cor das linhas
        for estilo in estilos_linhas:
            style.add(*estilo)
            
        table.setStyle(style)
        elements.append(table)
    
    # --- AJUSTE 4: Mover data de emissão para o rodapé ---
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 9)
        data_emissao_texto = f"Relatório emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, doc.bottomMargin / 2, data_emissao_texto)
        canvas.restoreState()

    # onFirstPage e onLaterPages garantem que o rodapé apareça em todas as páginas
    doc.build(elements, onFirstPage=footer, onLaterPages=footer)
    
    buffer.seek(0)
    return buffer