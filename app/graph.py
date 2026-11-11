from typing import Any, Dict, List, Literal, Optional
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import generate_mock_embedding, search_knowledge_chunks
from app.guardrails_service import security_guardrails
from app.models import Company, CustomerSession, MessageHistory, OrderRecord, utc_now
from app.rag import (
    build_system_prompt,
    get_chat_history,
    get_pizzaria_category_details,
    get_pizzaria_compact_greeting,
    get_pizzaria_full_menu_greeting,
    save_message,
    search_knowledge_base,
)
from app.schemas import BotState


async def resolve_company(state: BotState, config: RunnableConfig) -> Dict[str, Any]:
    """Identifica o tenant pela instance_name, recupera histórico conversacional e inicializa sessão."""
    db: AsyncSession = config["configurable"]["db"]
    instance_name = state.instance_name

    # Busca a empresa pelo instance_name único
    stmt = select(Company).where(Company.instance_name == instance_name, Company.active.is_(True))
    result = await db.execute(stmt)
    company = result.scalar_one_or_none()

    if not company:
        return {
            "company_id": "",
            "bot_response": "Empresa não cadastrada ou inativa no sistema.",
            "human_handoff": False,
        }

    # Carrega histórico recente (sliding window) no estado do bot
    history_messages = await get_chat_history(db, company.id, state.phone_number, limit=6)
    chat_hist_dicts = [
        {"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": str(m.content)}
        for m in history_messages
    ]

    # Busca ou cria a sessão do cliente para o tenant específico
    sess_stmt = select(CustomerSession).where(
        CustomerSession.company_id == company.id,
        CustomerSession.phone_number == state.phone_number
    )
    sess_res = await db.execute(sess_stmt)
    session = sess_res.scalar_one_or_none()

    updates: Dict[str, Any] = {
        "company_id": company.id,
        "owner_id": company.owner_id,
        "company_name": company.name,
        "segment": company.segment,
        "chat_history": chat_hist_dicts,
    }

    if session:
        sess_data = session.session_data or {}
        if state.current_step == "inicio" and session.current_step != "inicio":
            updates["current_step"] = session.current_step
        if not state.cart and sess_data.get("cart"):
            updates["cart"] = sess_data.get("cart", [])
        if not state.delivery_address and sess_data.get("delivery_address"):
            updates["delivery_address"] = sess_data.get("delivery_address")
        if not state.payment_method and sess_data.get("payment_method"):
            updates["payment_method"] = sess_data.get("payment_method")
    else:
        new_session = CustomerSession(
            company_id=company.id,
            phone_number=state.phone_number,
            customer_name=state.customer_name,
            current_step="inicio",
            session_data={}
        )
        db.add(new_session)
        await db.flush()

    return updates


def route_by_segment(state: BotState) -> str:
    """Roteador condicional dinâmico por segmento de atuação e etapa da conversa."""
    if not state.company_id:
        return "persist_session"

    if state.current_step == "ai_reasoning":
        return "ai_reasoning"

    segment = (state.segment or "").lower().strip()

    # Ramo Pizzaria e Lanches: atendimento conversacional humanizado e cognitivo
    if segment in ["pizzaria", "pizzaria_lanches", "lanchonete"]:
        return "ai_reasoning"

    # Ramo Delivery e Distribuidoras
    if segment in ["delivery", "distribuidora", "distribuidoras"]:
        step = state.current_step
        if step == "coletando_itens":
            return "collect_items"
        elif step == "coletando_endereco":
            return "collect_address"
        elif step == "coletando_pagamento":
            return "finalize_order"
        return "delivery_menu"

    # Ramo Balcão, Materiais de Construção, Farmácias
    return "balcao_triagem"


async def delivery_menu(state: BotState, config: RunnableConfig) -> Dict[str, Any]:
    """Menu principal e opções iniciais para o fluxo de Delivery/Distribuidoras."""
    text = (state.user_input or "").strip().lower()

    # Opção 1: Fazer Pedido
    if text == "1" or "fazer pedido" in text or "quero pedir" in text:
        return {
            "current_step": "coletando_itens",
            "bot_response": (
                "Excelente! Por favor, digite os itens e quantidades que deseja pedir.\n"
                "(Exemplo: 2 galões de água mineral 20L e 1 botijão de gás P13)"
            ),
            "human_handoff": False,
        }

    # Opção 2: Ver Preços
    if text == "2" or "preco" in text or "preço" in text or "tabela" in text or "catalogo" in text:
        return {
            "current_step": "inicio",
            "bot_response": (
                f"📋 *TABELA DE PREÇOS - {state.company_name}*\n\n"
                "• Água Mineral 20L: R$ 12,00\n"
                "• Gás de Cozinha P13: R$ 110,00\n"
                "• Cerveja Caixa (12 latas): R$ 42,00\n"
                "• Refrigerante 2L: R$ 9,00\n\n"
                "👉 Digite *1* para Fazer Pedido\n"
                "👉 Digite *3* para Falar com Atendente"
            ),
            "human_handoff": False,
        }

    # Opção 3: Falar com Atendente
    if text == "3" or "atendente" in text or "humano" in text or "suporte" in text or "falar com atendente" in text:
        return {
            "current_step": "inicio",
            "human_handoff": True,
            "bot_response": (
                "Entendido! Estou transferindo seu atendimento para um de nossos atendentes humanos. "
                "Por favor, aguarde um instante que já vamos te responder por aqui."
            ),
        }

    # Mensagem de Boas-Vindas Padrão
    return {
        "current_step": "inicio",
        "human_handoff": False,
        "bot_response": (
            f"Olá, {state.customer_name}! Seja muito bem-vindo(a) à *{state.company_name}*.\n\n"
            "Como posso te ajudar hoje?\n"
            "1. Fazer Pedido\n"
            "2. Ver Preços\n"
            "3. Falar com Atendente\n\n"
            "Digite o número da opção desejada:"
        ),
    }


async def collect_items(state: BotState, config: RunnableConfig) -> Dict[str, Any]:
    """Coleta e registra os itens do pedido no carrinho."""
    text = (state.user_input or "").strip()
    low = text.lower()

    if low in ["3", "atendente", "falar com atendente", "humano"]:
        return {
            "human_handoff": True,
            "bot_response": "Transferindo para um atendente humano agora mesmo...",
        }

    item_record = {"descricao": text, "quantidade": 1}
    updated_cart = list(state.cart)
    updated_cart.append(item_record)

    return {
        "cart": updated_cart,
        "current_step": "coletando_endereco",
        "human_handoff": False,
        "bot_response": (
            f"✅ Item adicionado ao carrinho: *{text}*\n\n"
            "Por favor, informe agora o seu *endereço completo para entrega* "
            "(Rua, Número, Bairro e Ponto de Referência)."
        ),
    }


async def collect_address(state: BotState, config: RunnableConfig) -> Dict[str, Any]:
    """Coleta e armazena o endereço de entrega do cliente."""
    text = (state.user_input or "").strip()
    low = text.lower()

    if low in ["3", "atendente", "falar com atendente", "humano"]:
        return {
            "human_handoff": True,
            "bot_response": "Transferindo para um atendente humano agora mesmo...",
        }

    return {
        "delivery_address": text,
        "current_step": "coletando_pagamento",
        "human_handoff": False,
        "bot_response": (
            f"📍 Endereço registrado: *{text}*\n\n"
            "Qual será a *forma de pagamento*?\n"
            "• Pix\n"
            "• Cartão de Crédito/Débito (na entrega)\n"
            "• Dinheiro (informe se precisa de troco)"
        ),
    }


async def finalize_order(state: BotState, config: RunnableConfig) -> Dict[str, Any]:
    """Coleta forma de pagamento, persiste o pedido e emite a comanda de expedição."""
    db: AsyncSession = config["configurable"]["db"]
    payment_method = (state.user_input or "Não informado").strip()

    items = state.cart if state.cart else [{"descricao": "Pedido Avulso", "quantidade": 1}]
    delivery_address = state.delivery_address or "Retirada no balcão"

    order = OrderRecord(
        company_id=state.company_id,
        customer_phone=state.phone_number,
        customer_name=state.customer_name,
        items=items,
        delivery_address=delivery_address,
        payment_method=payment_method,
        status="recebido"
    )
    db.add(order)
    await db.flush()

    items_formatted = "\n".join(f"  • {item.get('descricao', 'Item')} (Qtd: {item.get('quantidade', 1)})" for item in items)
    order_id_short = order.id[:8].upper()

    comanda = (
        "📋 *COMANDA DE EXPEDIÇÃO - PEDIDO CONFIRMADO* 📋\n"
        "────────────────────────────────────────\n"
        f"🏷️ *Pedido:* #{order_id_short}\n"
        f"🏢 *Empresa:* {state.company_name}\n"
        f"👤 *Cliente:* {state.customer_name} ({state.phone_number})\n"
        f"📍 *Entrega:* {delivery_address}\n"
        f"💳 *Pagamento:* {payment_method}\n"
        "📦 *Itens do Pedido:*\n"
        f"{items_formatted}\n"
        "────────────────────────────────────────\n"
        "🚀 *Status:* Recebido e em separação para entrega!\n\n"
        "Agradecemos a sua preferência! Qualquer dúvida, basta chamar."
    )

    return {
        "order_id": order.id,
        "payment_method": payment_method,
        "cart": [],
        "current_step": "inicio",
        "bot_response": comanda,
        "human_handoff": False,
    }


async def ai_reasoning_node(state: BotState, config: RunnableConfig) -> Dict[str, Any]:
    """
    Nó de Raciocínio Cognitivo via LangChain.
    Combina System Prompt dinâmico, contexto RAG do catálogo e histórico recente (sliding window).
    """
    db: AsyncSession = config["configurable"]["db"]
    text = (state.user_input or "").strip()

    # Busca a empresa para configurações e contexto
    stmt = select(Company).where(Company.id == state.company_id)
    res = await db.execute(stmt)
    company = res.scalar_one_or_none()

    if not company:
        return {"bot_response": "Empresa não cadastrada ou inativa.", "human_handoff": False}

    # 0. Inspeção de Segurança via NeMo Guardrails (Input Rails)
    guard_decision = security_guardrails.inspect_input(text, phone=state.phone_number, company_id=state.company_id)
    if not guard_decision.allowed:
        safe_resp = guard_decision.safe_response or "Solicitação não permitida pelas diretrizes de segurança da loja."
        await save_message(db, state.company_id, state.phone_number, role="user", content=text)
        await save_message(db, state.company_id, state.phone_number, role="assistant", content=safe_resp)
        return {
            "rag_context": None,
            "bot_response": safe_resp,
            "human_handoff": False,
        }

    # 1. Busca dinâmica no catálogo (RAG)
    rag_context = await search_knowledge_base(db, state.company_id, text, top_k=3)

    # 2. Constrói o System Prompt dinâmico
    system_prompt = state.system_prompt_override or build_system_prompt(company, rag_context)

    # 3. Recupera o histórico conversacional recente
    history_messages = await get_chat_history(db, state.company_id, state.phone_number, limit=6)

    # 4. Obtém o modelo LLM (Mockado ou Gemini 2.5 Flash)
    llm = config.get("configurable", {}).get("llm")
    if not llm:
        api_key = (company.settings or {}).get("gemini_api_key") or settings.GEMINI_API_KEY
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            api_version="v1beta",
            temperature=0.2,
            google_api_key=api_key,
        )

    # 5. Monta a sequência de mensagens
    messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
    messages.extend(history_messages)
    messages.append(HumanMessage(content=text))

    # 6. Executa a inferência assíncrona ou resposta determinística rápida de categoria
    low_input = text.lower().strip()
    is_pizzaria = company.segment in ["pizzaria_lanches", "pizzaria", "lanchonete"]

    # Atendimento imediato por número ou nome de categoria (1 a 5)
    category_answer = get_pizzaria_category_details(text) if is_pizzaria else None

    is_greeting_or_menu = (
        not text
        or any(
            w in low_input
            for w in [
                "oi", "olá", "ola", "boa noite", "bom dia", "boa tarde", "opa", "oie",
                "cardapio", "cardápio", "menu", "opcoes", "opções", "categorias"
            ]
        )
    )

    if category_answer:
        response_text = category_answer
    else:
        try:
            response = await llm.ainvoke(messages)
            response_text = response.content if hasattr(response, "content") else str(response)

            # Se for do segmento de pizzaria e o cliente apenas saudou ou pediu cardápio,
            # e a resposta gerada foi muito vaga (ex: "como posso ajudar"), injeta o menu compacto
            if is_pizzaria and is_greeting_or_menu:
                if len(response_text.strip()) < 120 or "como posso" in response_text.lower():
                    response_text = get_pizzaria_compact_greeting(company.name)
        except Exception:
            # Fallback inteligente se a chave ou conexão com o LLM não estiver disponível
            if is_pizzaria:
                if is_greeting_or_menu or not rag_context:
                    response_text = get_pizzaria_compact_greeting(company.name)
                else:
                    clean_context = (rag_context or "").replace("*", "")
                    response_text = (
                        f"Olá! Que bom falar com você! 🍕 Temos essas opções no nosso cardápio:\n\n"
                        f"{clean_context}\n\n"
                        "Qual você vai escolher hoje? Se desejar, também temos bebidas bem geladas para acompanhar!"
                    )
            else:
                clean_context = (rag_context or "").replace("*", "")
                if rag_context:
                    response_text = (
                        f"Olá! Localizei as seguintes informações em nosso catálogo ({company.name}):\n\n"
                        f"{clean_context}\n\n"
                        "Como posso te ajudar a dar sequência no pedido?"
                    )
                else:
                    response_text = f"Olá! Bem-vindo à {company.name}. Como posso te ajudar hoje?"

    # Limpeza de asteriscos se exigido pelas diretrizes do segmento
    if company.segment in ["pizzaria_lanches", "pizzaria", "lanchonete"]:
        response_text = response_text.replace("*", "")

    # 7. Output Rails: Inspeciona e sanitiza a resposta contra vazamento de credenciais e segredos
    response_text = security_guardrails.inspect_output(response_text, phone=state.phone_number, company_id=state.company_id)

    # 8. Persiste a interação no histórico
    await save_message(db, state.company_id, state.phone_number, role="user", content=text)
    await save_message(db, state.company_id, state.phone_number, role="assistant", content=response_text)

    # 9. Avalia ativação de transbordo humano
    low_resp = response_text.lower()
    low_input = text.lower()
    needs_handoff = (
        any(k in low_input for k in ["atendente", "falar com humano", "cancelamento", "reclamar", "problema no pedido", "cancelar pedido"])
        or (
            not rag_context
            and any(w in low_input for w in ["locação", "guindaste", "especialista", "técnico", "fora do catálogo"])
        )
        or any(w in low_resp for w in ["atendente humano", "especialista no balcão", "transferindo para", "equipe do balcão"])
    )

    return {
        "rag_context": rag_context,
        "bot_response": response_text,
        "human_handoff": needs_handoff,
    }


# Alias para conformidade com diferentes referências
ai_reasoning = ai_reasoning_node


async def balcao_triagem(state: BotState, config: RunnableConfig) -> Dict[str, Any]:
    """Ramo Balcão/Materiais/Farmácias com RAG de catálogo e suporte a LLM cognitivo."""
    # Se houver um LLM injetado ou solicitação explícita de IA, delega para ai_reasoning_node
    if config.get("configurable", {}).get("llm") is not None:
        return await ai_reasoning_node(state, config)

    db: AsyncSession = config["configurable"]["db"]
    text = (state.user_input or "").strip()
    low = text.lower()

    # Transbordo manual explícito
    if any(k in low for k in ["atendente", "vendedor", "humano", "balcao", "balcão", "falar com atendente", "especialista"]):
        return {
            "human_handoff": True,
            "bot_response": (
                "Entendido! Estou acionando nosso vendedor no balcão agora mesmo. "
                "Por favor, aguarde um instante que ele já vai te responder por aqui."
            ),
        }

    # Busca RAG no catálogo da empresa
    query_vector = generate_mock_embedding(text)
    matched_chunks = await search_knowledge_chunks(
        db=db,
        company_id=state.company_id,
        query_embedding=query_vector,
        top_k=2,
        min_similarity=0.10
    )

    # Se localizou itens relevantes no catálogo
    if matched_chunks:
        rag_parts = []
        for chunk, score in matched_chunks:
            meta = chunk.metadata_info or {}
            price = f" - Preço: R$ {meta.get('preco')}" if 'preco' in meta else ""
            rag_parts.append(f"• *{chunk.content}*{price}")

        rag_text = "\n".join(rag_parts)
        response_msg = (
            f"Localizei as seguintes opções em nosso catálogo ({state.company_name}):\n\n"
            f"{rag_text}\n\n"
            "Deseja fechar o pedido desses itens ou precisa de mais algum detalhe técnico? "
            "(Digite *atendente* caso queira falar diretamente com nosso especialista no balcão)."
        )
        return {
            "rag_context": rag_text,
            "human_handoff": False,
            "bot_response": response_msg,
        }

    # Fallback técnico imediato com ativação de transbordo humano
    return {
        "rag_context": None,
        "human_handoff": True,
        "bot_response": (
            "Não localizei essa especificação exata em nosso catálogo imediato. "
            "Estou transferindo sua solicitação diretamente para o nosso especialista no balcão para te atender agora mesmo."
        ),
    }


async def persist_session(state: BotState, config: RunnableConfig) -> Dict[str, Any]:
    """Persiste histórico conversacional e atualizações da sessão no banco de dados."""
    db: AsyncSession = config["configurable"]["db"]
    if not state.company_id:
        return {}

    # Persiste no MessageHistory se ainda não foi salvo neste ciclo
    if state.user_input and state.bot_response:
        stmt_last = (
            select(MessageHistory)
            .where(
                MessageHistory.company_id == state.company_id,
                MessageHistory.customer_phone == state.phone_number
            )
            .order_by(MessageHistory.created_at.desc())
            .limit(1)
        )
        res_last = await db.execute(stmt_last)
        last_msg = res_last.scalar_one_or_none()

        # Evita duplicar se ai_reasoning_node já persistiu
        if not last_msg or last_msg.content != state.bot_response:
            await save_message(db, state.company_id, state.phone_number, role="user", content=state.user_input)
            await save_message(db, state.company_id, state.phone_number, role="assistant", content=state.bot_response)

    # Atualiza a sessão
    stmt = select(CustomerSession).where(
        CustomerSession.company_id == state.company_id,
        CustomerSession.phone_number == state.phone_number
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session:
        session.current_step = state.current_step
        session.customer_name = state.customer_name
        session.last_interaction = utc_now()
        session.session_data = {
            "cart": state.cart,
            "delivery_address": state.delivery_address,
            "payment_method": state.payment_method,
            "order_id": state.order_id,
            "human_handoff": state.human_handoff,
        }
        await db.flush()

    return {}


def build_bot_graph():
    """Constrói e compila a máquina de estados adaptativa multi-tenant via LangGraph."""
    graph = StateGraph(BotState)

    # Adição de Nós
    graph.add_node("resolve_company", resolve_company)
    graph.add_node("delivery_menu", delivery_menu)
    graph.add_node("collect_items", collect_items)
    graph.add_node("collect_address", collect_address)
    graph.add_node("finalize_order", finalize_order)
    graph.add_node("balcao_triagem", balcao_triagem)
    graph.add_node("ai_reasoning", ai_reasoning_node)
    graph.add_node("persist_session", persist_session)

    # Fluxo inicial
    graph.add_edge(START, "resolve_company")

    # Roteador condicional por segmento e passo
    graph.add_conditional_edges(
        "resolve_company",
        route_by_segment,
        {
            "delivery_menu": "delivery_menu",
            "collect_items": "collect_items",
            "collect_address": "collect_address",
            "finalize_order": "finalize_order",
            "balcao_triagem": "balcao_triagem",
            "ai_reasoning": "ai_reasoning",
            "persist_session": "persist_session",
        }
    )

    # Transições do Ramo Delivery
    graph.add_edge("delivery_menu", "persist_session")
    graph.add_edge("collect_items", "persist_session")
    graph.add_edge("collect_address", "persist_session")
    graph.add_edge("finalize_order", "persist_session")

    # Transições do Ramo Balcão e Cognitivo
    graph.add_edge("balcao_triagem", "persist_session")
    graph.add_edge("ai_reasoning", "persist_session")

    # Término do fluxo
    graph.add_edge("persist_session", END)

    return graph.compile()
