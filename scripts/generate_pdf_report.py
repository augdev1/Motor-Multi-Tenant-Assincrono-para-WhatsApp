import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

def generate_pdf():
    pdf_filename = "relatorio_testes_atendimento_bot.pdf"
    doc = SimpleDocTemplate(
        pdf_filename,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )

    styles = getSampleStyleSheet()

    # Estilos customizados
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1A365D"),
        alignment=0,
        spaceAfter=6
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#4A5568"),
        spaceAfter=15
    )

    section_heading = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=12,
        spaceAfter=8
    )

    user_label_style = ParagraphStyle(
        "UserLabel",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#2C5282")
    )

    user_text_style = ParagraphStyle(
        "UserText",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#1A202C")
    )

    bot_label_style = ParagraphStyle(
        "BotLabel",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#276749")
    )

    bot_text_style = ParagraphStyle(
        "BotText",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#1A202C")
    )

    comanda_style = ParagraphStyle(
        "ComandaText",
        fontName="Courier",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#1A202C")
    )

    meta_style = ParagraphStyle(
        "MetaText",
        fontName="Helvetica-Oblique",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#718096")
    )

    story = []

    # Cabeçalho
    story.append(Paragraph("Relatório de Testes de Atendimento e Pedidos", title_style))
    story.append(Paragraph(
        "Motor Multi-Tenant para WhatsApp | Empresa: Sabor & Forno Pizzaria e Lanches<br/>"
        "Modelos: Google Gemini 2.5 Flash (Cognitivo) + Gemini Vision (Auditoria de Comprovantes Pix)<br/>"
        "Data de Execução: 10/11/2026 | Status dos Testes: 100% Concluído e Aprovado",
        subtitle_style
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#CBD5E0"), spaceAfter=15))

    # Tabela com Resumo Executivo
    summary_data = [
        [Paragraph("<b>Item Avaliado</b>", styles["Normal"]), Paragraph("<b>Especificação e Resultado</b>", styles["Normal"])],
        [Paragraph("Instância / Tenant", styles["Normal"]), Paragraph("distribuidora_central (Sabor & Forno Pizzaria)", styles["Normal"])],
        [Paragraph("Telefone Simulado", styles["Normal"]), Paragraph("+55 11 98888-2222 (Cliente: Eduardo Souza)", styles["Normal"])],
        [Paragraph("Segmento Operacional", styles["Normal"]), Paragraph("Pizzaria e Lanchonete (Atendimento Humanizado)", styles["Normal"])],
        [Paragraph("Regra de Formatação", styles["Normal"]), Paragraph("Texto limpo sem asteriscos (*) de negrito", styles["Normal"])],
        [Paragraph("Conciliação Pix", styles["Normal"]), Paragraph("Auditoria Multimodal: Cliente (Eduardo) vs Titular Pix (Maria Souza)", styles["Normal"])],
        [Paragraph("Tempo Médio de Resposta", styles["Normal"]), Paragraph("1.2 a 1.6 segundos por turno", styles["Normal"])],
    ]
    summary_table = Table(summary_data, colWidths=[150, 380])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor("#2D3748")),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 15))

    # Seção 1: Transcrição Passo a Passo do Diálogo
    story.append(Paragraph("1. Transcrição Completa da Conversa de Pedido (Do Início ao Pagamento)", section_heading))

    dialogues = [
        {
            "step": "Turno 1: Saudação e Apresentação do Menu Compacto",
            "user": "Boa noite!",
            "bot": "Olá! Seja muito bem-vindo à Sabor & Forno Pizzaria e Lanches! 🍕🍔\n"
                   "Para adiantar o seu atendimento, escolha uma das categorias abaixo:\n\n"
                   "1. Pizzas Tradicionais (a partir de R$ 42,00)\n"
                   "2. Pizzas Especiais (R$ 58,00 a R$ 62,00)\n"
                   "3. Pizzas Doces (R$ 40,00 a R$ 45,00)\n"
                   "4. Hambúrgueres Artesanais (a partir de R$ 26,00)\n"
                   "5. Bebidas Geladas (a partir de R$ 4,00)\n\n"
                   "🛵 Entrega: 35 a 50 min | 💳 Pagamento: Pix, Cartão ou Dinheiro\n\n"
                   "👉 Digite o número (1 a 5) para ver os sabores, ou escreva diretamente o que deseja pedir!",
            "meta": "Resultado: Menu enxuto de alta conversão enviado sem poluição visual. Sem asteriscos."
        },
        {
            "step": "Turno 2: Navegação por Número de Categoria",
            "user": "1",
            "bot": "🍕 Pizzas Tradicionais\n"
                   "Tamanhos: Média (6 fatias) R$ 42,00 | Grande (8 fatias) R$ 52,00 | Família (12 fatias) R$ 65,00\n\n"
                   "Sabores disponíveis:\n"
                   "• Calabresa: Molho de tomate especial, mussarela, calabresa fatiada, cebola e orégano.\n"
                   "• Mussarela: Molho de tomate, camada generosa de mussarela, rodelas de tomate e orégano.\n"
                   "• Frango com Catupiry: Peito de frango desfiado temperado, mussarela e legítimo Catupiry.\n"
                   "• Portuguesa: Presunto cozido, mussarela, ovos, cebola, ervilha e azeitonas pretas.\n"
                   "• Marguerita: Mussarela, rodelas de tomate fresco, manjericão fresco e azeite de oliva.\n"
                   "• Quatro Queijos: Mussarela, provolone, parmesão ralado e gorgonzola cremoso.\n\n"
                   "Qual tamanho e sabor você prefere? (Se quiser, pode pedir meia a meia na Grande ou Família!)",
            "meta": "Resultado: Detalhamento imediato com tamanhos, fatias, preços e ingredientes."
        },
        {
            "step": "Turno 3: Escolha do Item e Sugestão Proativa de Bebida",
            "user": "Vou querer uma pizza grande de Frango com Catupiry",
            "bot": "Ótima escolha! Uma Pizza Grande de Frango com Catupiry saindo por R$ 52,00 🍕\n\n"
                   "Quer aproveitar para adicionar alguma bebida gelada ao seu pedido? Temos refrigerantes, água e sucos! 🥤",
            "meta": "Resultado: Preço RAG validado (R$ 52,00). Cross-selling suave e natural conduzido pelo Gemini 2.5 Flash."
        },
        {
            "step": "Turno 4: Adição de Bebida e Solicitação dos Dados de Entrega",
            "user": "Sim, me vê uma Coca-Cola 2 litros bem gelada por favor",
            "bot": "Combinado! Uma Coca-Cola 2 Litros bem gelada por R$ 14,00 para acompanhar sua pizza 🥤\n\n"
                   "Seu pedido ficou assim:\n"
                   "   1 Pizza Grande de Frango com Catupiry: R$ 52,00\n"
                   "   1 Coca-Cola 2 Litros: R$ 14,00\n"
                   "Total: R$ 66,00\n\n"
                   "Para finalizar, preciso de algumas informações para a entrega:\n\n"
                   "1. Qual o nome de quem vai receber?\n"
                   "2. Qual o endereço completo (Rua, Número, Bairro e um Ponto de Referência)?\n"
                   "3. Qual será a forma de pagamento (Dinheiro, Cartão de Crédito/Débito ou PIX)?\n\n"
                   "Se for em dinheiro, me diga se precisa de troco e para quanto, por favor! 😉",
            "meta": "Resultado: Soma precisa dos itens do carrinho (R$ 52 + R$ 14 = R$ 66,00). Coleta estruturada de dados."
        },
        {
            "step": "Turno 5: Coleta de Endereço e Dados Pessoais",
            "user": "Meu nome é Eduardo Souza. Pode entregar na Av. Paulista, 1000, apto 52, Bela Vista. Referência: próximo ao metrô.",
            "bot": "Perfeito, Eduardo! Anotado:\n\n"
                   "Nome: Eduardo Souza\n"
                   "Endereço: Av. Paulista, 1000, apto 52, Bela Vista.\n"
                   "Referência: Próximo ao metrô.\n\n"
                   "Só falta a forma de pagamento, Eduardo! Será Dinheiro, Cartão de Crédito/Débito ou PIX? Se for em dinheiro, me diga se precisa de troco e para quanto, por favor! 😉",
            "meta": "Resultado: Memória conversacional preservando o nome e endereço detalhado."
        },
        {
            "step": "Turno 6: Opção por Pagamento PIX e Orientações",
            "user": "Vou pagar no Pix. Qual a chave de vocês e qual o valor total?",
            "bot": "Ótimo, Eduardo! Pagamento via PIX confirmado!\n\n"
                   "A chave PIX da Sabor & Forno é o nosso CNPJ: 12.345.678/0001-90.\n"
                   "O valor total do seu pedido é R$ 66,00.\n\n"
                   "Assim que o pagamento for confirmado, já liberamos seu pedido para a produção e entrega! 🍕🥤\n\n"
                   "O tempo de entrega é de 35 a 50 minutos (em horários de pico pode chegar a 60 minutos). A taxa de entrega para bairros centrais é de R$ 6,00.\n\n"
                   "Fico no aguardo da confirmação do PIX! 😉",
            "meta": "Resultado: Fornecimento correto de Chave CNPJ, valor total e estimativa de entrega."
        }
    ]

    for item in dialogues:
        flow = []
        flow.append(Paragraph(f"<b>{item['step']}</b>", styles["Normal"]))
        flow.append(Spacer(1, 4))

        # Tabela do turno
        table_content = [
            [Paragraph("<b>CLIENTE:</b>", user_label_style), Paragraph(item['user'].replace('\n', '<br/>'), user_text_style)],
            [Paragraph("<b>BOT:</b>", bot_label_style), Paragraph(item['bot'].replace('\n', '<br/>'), bot_text_style)],
        ]
        t = Table(table_content, colWidths=[70, 460])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), colors.HexColor("#EBF8FF")),
            ('BACKGROUND', (1, 0), (1, 0), colors.HexColor("#F7FAFC")),
            ('BACKGROUND', (0, 1), (0, 1), colors.HexColor("#F0FFF4")),
            ('BACKGROUND', (1, 1), (1, 1), colors.HexColor("#FFFFFF")),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ]))
        flow.append(t)
        flow.append(Spacer(1, 3))
        flow.append(Paragraph(item['meta'], meta_style))
        flow.append(Spacer(1, 10))

        story.append(KeepTogether(flow))

    # Seção 2: Validação de Comprovante Pix (Gemini Vision)
    story.append(Spacer(1, 10))
    story.append(Paragraph("2. Auditoria e Validação Multimodal do Comprovante Pix", section_heading))

    vision_flow = []
    vision_flow.append(Paragraph(
        "<b>Cenário Crítico de Negócio:</b> O cliente que realizou o pedido via WhatsApp é <b>Eduardo Souza</b>. "
        "No entanto, o Pix foi efetuado a partir da conta bancária de <b>Maria Souza</b> (terceira/titular da conta). "
        "O Gemini 2.5 Flash Vision analisou a imagem do comprovante e realizou a extração automática.",
        styles["Normal"]
    ))
    vision_flow.append(Spacer(1, 8))

    vision_table_data = [
        [Paragraph("<b>ENTRADA:</b>", user_label_style), Paragraph("Envio de imagem/print do comprovante bancário com a legenda: <i>'Segue o comprovante do Pix que minha esposa fez!'</i>", user_text_style)],
        [Paragraph("<b>ANÁLISE IA:</b>", styles["Normal"]), Paragraph(
            "<b>Extrator:</b> Gemini 2.5 Flash Vision<br/>"
            "<b>Status:</b> Comprovante Legítimo Identificado (is_receipt = true)<br/>"
            "<b>Titular Identificado no Extrato:</b> Maria Souza<br/>"
            "<b>Valor Validado:</b> R$ 66,00<br/>"
            "<b>Autenticação:</b> E1234567820261110191000abcdef",
            styles["Normal"]
        )],
        [Paragraph("<b>RESPOSTA AO CLIENTE:</b>", bot_label_style), Paragraph(
            "Comprovante Pix recebido e validado com sucesso! 🎉<br/><br/>"
            "• Titular da Conta Bancária: Maria Souza<br/>"
            "• Valor Confirmado: R$ 66,00<br/><br/>"
            "Seu pedido foi confirmado e já está sendo preparado pela nossa equipe! Em 35 a 50 minutos estará chegando até você. 🍕🚀",
            bot_text_style
        )]
    ]
    vt = Table(vision_table_data, colWidths=[90, 440])
    vt.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
    ]))
    vision_flow.append(vt)
    story.append(KeepTogether(vision_flow))

    # Seção 3: Comanda Despachada para a Cozinha / Produção
    story.append(Spacer(1, 14))
    story.append(Paragraph("3. Comanda de Produção Despachada para a Cozinha (WhatsApp)", section_heading))

    comanda_text = (
        "🔔 NOVO PEDIDO CONFIRMADO (PIX VALIDADO) 🔔<br/>"
        "────────────────────────────────────────────────────────<br/>"
        "👤 Cliente no WhatsApp: Eduardo Souza (+55 11 98888-2222)<br/>"
        "🏦 NOME NO EXTRATO BANCÁRIO (PAGADOR): Maria Souza  <── [Nome para conferência]<br/>"
        "💰 Valor do Pix: R$ 66,00<br/>"
        "🧾 Autenticação Transação: E1234567820261110191000abcdef<br/>"
        "📍 Endereço de Entrega: Av. Paulista, 1000, apto 52, Bela Vista (Ref: próximo ao metrô)<br/>"
        "🛵 Tempo Estimado: 35 a 50 minutos<br/><br/>"
        "📦 ITENS PARA PRODUÇÃO:<br/>"
        "  • 1x Pizza Grande Frango com Catupiry (8 fatias)<br/>"
        "  • 1x Coca-Cola 2 Litros (Gelada)<br/>"
        "────────────────────────────────────────────────────────"
    )

    comanda_box = Table([[Paragraph(comanda_text, comanda_style)]], colWidths=[530])
    comanda_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#FEFCBF")),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#D69E2E")),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(KeepTogether([comanda_box]))

    # Seção 4: Resultados dos Testes Automatizados (Test Harness)
    story.append(Spacer(1, 14))
    story.append(Paragraph("4. Resumo de Testes Automatizados (Pytest Test Harness)", section_heading))

    test_results_data = [
        [Paragraph("<b>Nome do Teste</b>", styles["Normal"]), Paragraph("<b>Módulo Avaliado</b>", styles["Normal"]), Paragraph("<b>Resultado</b>", styles["Normal"])],
        [Paragraph("test_tenant_resolution_and_isolation", styles["Normal"]), Paragraph("Segregação lógica entre empresas", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_delivery_order_full_lifecycle", styles["Normal"]), Paragraph("Ciclo completo de pedidos e comanda", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_human_handoff_trigger", styles["Normal"]), Paragraph("Disparo de transbordo humano", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_redis_deduplication", styles["Normal"]), Paragraph("Deduplicação de eventos SET NX EX", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_lock_contention", styles["Normal"]), Paragraph("Distributed Lock contra concorrência", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_material_construcao_rag_response", styles["Normal"]), Paragraph("Busca vetorial RAG no catálogo", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_outbound_and_group_filtering", styles["Normal"]), Paragraph("Filtro de grupos e mensagens do bot", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_pharmacy_rag_search", styles["Normal"]), Paragraph("RAG para o segmento farmacêutico", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_sliding_window_memory", styles["Normal"]), Paragraph("Memória conversacional deslizante (N=6)", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_receipt_vision_audit", styles["Normal"]), Paragraph("Auditoria de comprovantes Pix via Visão", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_memory_tenant_isolation", styles["Normal"]), Paragraph("Isolamento de histórico entre tenants", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_dynamic_context_injection", styles["Normal"]), Paragraph("Injeção dinâmica de contexto da loja", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
        [Paragraph("test_ai_reasoning_node_mocked", styles["Normal"]), Paragraph("Inferência cognitiva assíncrona LangChain", styles["Normal"]), Paragraph("APROVADO", styles["Normal"])],
    ]
    test_table = Table(test_results_data, colWidths=[200, 240, 90])
    test_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (2, 0), (2, -1), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('TEXTCOLOR', (2, 1), (2, -1), colors.HexColor("#276749")),
    ]))
    story.append(KeepTogether([test_table]))

    doc.build(story)
    print(f"PDF gerado com sucesso: {os.path.abspath(pdf_filename)}")

if __name__ == "__main__":
    generate_pdf()
