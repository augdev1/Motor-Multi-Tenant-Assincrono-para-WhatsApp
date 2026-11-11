from datetime import datetime, timedelta, timezone
from typing import List, Optional
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import search_knowledge_chunks
from app.embeddings import generate_semantic_embedding
from app.models import Company, MessageHistory

_last_saved_ts: Optional[datetime] = None


def _next_monotonic_utc() -> datetime:
    """Garante timestamps estritamente crescentes mesmo em gravações sub-milissegundo."""
    global _last_saved_ts
    now = datetime.now(timezone.utc)
    if _last_saved_ts is not None and now <= _last_saved_ts:
        now = _last_saved_ts + timedelta(microseconds=10)
    _last_saved_ts = now
    return now


async def get_chat_history(
    session: AsyncSession,
    company_id: str,
    phone: str,
    limit: int = 6
) -> List[BaseMessage]:
    """
    Recupera as últimas N mensagens do cliente na empresa com janela deslizante (sliding window).
    Garante isolamento multi-tenant estrito por company_id e phone.
    Retorna a lista ordenada cronologicamente (da mais antiga para a mais recente).
    """
    stmt = (
        select(MessageHistory)
        .where(
            MessageHistory.company_id == company_id,
            MessageHistory.customer_phone == phone
        )
        .order_by(MessageHistory.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())

    # Inverte para manter ordem cronológica crescente
    records.reverse()

    messages: List[BaseMessage] = []
    for rec in records:
        if rec.role == "user":
            messages.append(HumanMessage(content=rec.content))
        elif rec.role == "assistant":
            messages.append(AIMessage(content=rec.content))

    return messages


async def save_message(
    session: AsyncSession,
    company_id: str,
    phone: str,
    role: str,
    content: str
) -> None:
    """Persiste uma mensagem no histórico conversacional da empresa."""
    if not company_id or not phone or not content:
        return

    msg = MessageHistory(
        company_id=company_id,
        customer_phone=phone,
        role=role,
        content=content,
        created_at=_next_monotonic_utc(),
    )
    session.add(msg)
    await session.flush()


def get_pizzaria_compact_greeting(company_name: str = "Sabor & Forno Pizzaria e Lanches") -> str:
    """
    Retorna a saudação compacta e elegante com menu por tópicos e faixa de preço,
    evitando a sobrecarga de texto no celular e adiantando o pedido do cliente.
    """
    return (
        f"Olá! Seja muito bem-vindo à {company_name}! 🍕🍔\n"
        "Para adiantar o seu atendimento, escolha uma das categorias abaixo:\n\n"
        "1. Pizzas Tradicionais (a partir de R$ 42,00)\n"
        "2. Pizzas Especiais (R$ 58,00 a R$ 62,00)\n"
        "3. Pizzas Doces (R$ 40,00 a R$ 45,00)\n"
        "4. Hambúrgueres Artesanais (a partir de R$ 26,00)\n"
        "5. Bebidas Geladas (a partir de R$ 4,00)\n\n"
        "🛵 Entrega: 35 a 50 min | 💳 Pagamento: Pix, Cartão ou Dinheiro\n\n"
        "👉 Digite o número (1 a 5) para ver os sabores, ou escreva diretamente o que deseja pedir!"
    )


def get_pizzaria_category_details(category_key: str) -> Optional[str]:
    """
    Retorna os sabores, tamanhos e preços detalhados de cada categoria
    quando o cliente digita o número (1 a 5) ou o nome do tópico.
    """
    cat = category_key.strip().lower()
    if cat in ["1", "1.", "pizzas tradicionais", "tradicionais", "tradicional"]:
        return (
            "🍕 Pizzas Tradicionais\n"
            "Tamanhos: Média (6 fatias) R$ 42,00 | Grande (8 fatias) R$ 52,00 | Família (12 fatias) R$ 65,00\n\n"
            "Sabores disponíveis:\n"
            "• Calabresa: Molho de tomate especial, mussarela, calabresa fatiada, cebola e orégano.\n"
            "• Mussarela: Molho de tomate, camada generosa de mussarela, rodelas de tomate e orégano.\n"
            "• Frango com Catupiry: Peito de frango desfiado temperado, mussarela e legítimo Catupiry.\n"
            "• Portuguesa: Presunto cozido, mussarela, ovos, cebola, ervilha e azeitonas pretas.\n"
            "• Marguerita: Mussarela, rodelas de tomate fresco, manjericão fresco e azeite de oliva.\n"
            "• Quatro Queijos: Mussarela, provolone, parmesão ralado e gorgonzola cremoso.\n\n"
            "Qual tamanho e sabor você prefere? (Se quiser, pode pedir meia a meia na Grande ou Família!)"
        )
    elif cat in ["2", "2.", "pizzas especiais", "especiais", "especial"]:
        return (
            "⭐ Pizzas Especiais (Tamanho Grande - 8 fatias)\n\n"
            "• Carne de Sol na Nata (R$ 62,00): Carne de sol desfiada, nata fresca, mussarela e cebola roxa.\n"
            "• Bacon Crocante (R$ 58,00): Mussarela, fatias crocantes de bacon e barbecue artesanal.\n\n"
            "Qual dessas você vai querer hoje?"
        )
    elif cat in ["3", "3.", "pizzas doces", "doces", "doce"]:
        return (
            "🍫 Pizzas Doces (Tamanho Média - 6 fatias)\n\n"
            "• Chocolate com Morango (R$ 45,00): Chocolate ao leite derretido e fatias de morango fresco.\n"
            "• Romeu e Julieta (R$ 40,00): Mussarela e goiabada cremosa.\n\n"
            "Qual delícia doce você escolhe para hoje?"
        )
    elif cat in ["4", "4.", "hambúrgueres", "hamburgueres", "hamburguer", "lanches", "lanche", "smash", "burgers", "burger"]:
        return (
            "🍔 Smash Burgers Artesanais (Acompanham batata frita ou rústica)\n\n"
            "• Smash Clássico (R$ 28,00): Pão brioche, 2x smash burger 90g, queijo cheddar e molho da casa.\n"
            "• Smash Bacon Burger (R$ 32,00): Pão brioche, 2x smash burger 90g, cheddar, fatias de bacon e cebola caramelizada.\n"
            "• Smash Frango Crispy (R$ 26,00): Pão brioche, sobrecoxa empanada crocante, alface americana e maionese de ervas.\n\n"
            "Qual burger você escolhe?"
        )
    elif cat in ["5", "5.", "bebidas", "bebida", "refrigerante", "refrigerantes", "cervejas", "cerveja", "agua", "água"]:
        return (
            "🥤 Bebidas Geladas\n\n"
            "• Refrigerantes Lata 350ml (R$ 6,00): Coca-Cola, Guaraná Antarctica, Fanta Laranja.\n"
            "• Refrigerantes 2 Litros (R$ 12,00): Coca-Cola, Guaraná Antarctica.\n"
            "• Sucos Naturais Del Valle 400ml (R$ 8,00): Laranja, Uva, Maracujá.\n"
            "• Cervejas Long Neck (R$ 9,00): Heineken, Budweiser.\n"
            "• Água Mineral 500ml (R$ 4,00): com ou sem gás.\n\n"
            "Qual bebida vai acompanhar o seu pedido?"
        )
    return None


def get_pizzaria_full_menu_greeting(company_name: str = "Sabor & Forno Pizzaria e Lanches") -> str:
    """Alias para manter retrocompatibilidade apontando para a saudação compacta."""
    return get_pizzaria_compact_greeting(company_name)


def build_system_prompt(company: Company, rag_context: Optional[str]) -> str:
    """
    Constrói dinamicamente o System Prompt combinando a identidade da loja,
    diretrizes de atendimento humanizado (prompt_principal), políticas operacionais
    e a base de dados do catálogo RAG (rag_pizzaria).
    """
    settings = company.settings or {}
    horario = settings.get("horario_funcionamento", "Terça a Domingo, das 18h às 23h30 (Segunda-feira fechado)")
    entrega = settings.get("regras_entrega", "Tempo de entrega: 35 a 50 minutos (pico até 60 min). Taxa: R$ 6,00 bairros centrais | R$ 10,00 a R$ 14,00 demais bairros.")
    chave_pix = settings.get("chave_pix", "PIX CNPJ: 12.345.678/0001-90 - Sabor & Forno")
    politicas_extras = settings.get("politicas", "Cartão de Crédito/Débito (motoboy leva maquininha) | Dinheiro com troco informado")

    catalog_section = (
        f"BASE DE DADOS E CONHECIMENTO DA LANCHONETE & PIZZARIA (RAG):\n{rag_context}"
        if rag_context
        else "BASE DE DADOS: Nenhum item com correspondência exata localizado no catálogo para esta consulta."
    )

    prompt = f"""Você é o atendente virtual do WhatsApp da empresa "{company.name}".
Segmento de atuação: {company.segment}.

Seu objetivo é conduzir um atendimento ágil, educado, empático e totalmente natural, como se fosse um profissional experiente atendendo no balcão da loja.

INFORMAÇÕES OPERACIONAIS:
- Horário de Funcionamento: {horario}
- Regras de Entrega: {entrega}
- Informações de Pagamento / PIX: {chave_pix}
- Políticas Gerais: {politicas_extras}

{catalog_section}

DIRETRIZES DE POSTURA E COMUNICAÇÃO:

1. Saudação Inicial com Cardápio Completo e Estruturado:
   - No primeiro contato do cliente (saudações como "olá", "boa noite", "bom dia", "opa", "oie", "cardápio", etc.) ou quando ele pedir para ver as opções, NUNCA dê uma resposta vaga ou vazia (como apenas "como posso te ajudar?"). Apresente sempre a saudação acolhedora acompanhada dos tópicos numerados (1. Pizzas Tradicionais, 2. Pizzas Especiais, 3. Pizzas Doces, 4. Smash Burgers Artesanais, 5. Bebidas Geladas) com todos os seus subtópicos detalhados (sabores, tamanhos e preços) para adiantar ao máximo a escolha do cliente.

2. Zero Linguagem Robótica:
   - Não force menus rígidos de navegação (ex: "Digite [1] para voltar"). Permita que o cliente escolha pelo número ou escreva naturalmente o nome dos sabores e produtos.
   - Use frases receptivas e emojis moderados (🍕, 🍔, 🥤, 🛵) para dar ritmo de conversa real de WhatsApp.

2. Flexibilidade Total e Transição Dinâmica:
   - Responda prontamente quando o cliente mudar de ideia no meio do atendimento. Se o assunto for hambúrguer e ele perguntar sobre pizzas doces, apresente as opções de pizza imediatamente sem travar nem insistir no tópico anterior.
   - Permita pedidos combinados no mesmo carrinho (exemplo: uma pizza média salgada e um hambúrguer smash).

3. Etapas de Fechamento Suave:
   - Quando o cliente escolher o alimento, pergunte de forma leve se deseja adicionar uma bebida gelada (refrigerante, água ou suco).
   - Para concluir o pedido, certifique-se de obter:
     a) Nome de quem vai receber.
     b) Endereço completo (Rua, Número, Bairro e Ponto de Referência).
     c) Forma de pagamento (se escolher dinheiro, pergunte se precisa de troco e para quanto).

4. Precisão do Cardápio (Sem Alucinações):
   - PROIBIÇÃO RIGOROSA: Nunca invente, alucine ou deduza preços, produtos ou especificações técnicas. Consulte sempre as informações oficiais da base de conhecimento acima para informar preços, ingredientes e tamanhos.
   - Se o cliente solicitar itens que a loja não comercializa, explique de forma cordial que não estão no cardápio e sugira as alternativas disponíveis.

5. Transbordo para Atendente Humano:
   - Se o cliente solicitar expressamente falar com um atendente humano, relatar um problema com pedido anterior ou solicitar cancelamento, confirme educadamente que a equipe do balcão está sendo notificada para assumir o chat.

6. Formatação Limpa sem Asteriscos:
   - Sempre responda e entregue as respostas de forma correta, educada, assertiva e formatada sem asteriscos (não utilize caracteres de asterisco para negrito).
"""
    return prompt.strip()


async def search_knowledge_base(
    session: AsyncSession,
    company_id: str,
    query: str,
    top_k: int = 3,
    min_similarity: float = 0.10,
    redis_client=None,
) -> str:
    """
    Executa busca vetorial no catálogo do tenant e retorna o contexto formatado em texto.
    """
    if not query:
        return ""

    query_embedding = await generate_semantic_embedding(query, redis_client=redis_client)
    matches = await search_knowledge_chunks(
        db=session,
        company_id=company_id,
        query_embedding=query_embedding,
        top_k=top_k,
        min_similarity=min_similarity
    )

    if not matches:
        return ""

    chunks_text: List[str] = []
    for chunk, score in matches:
        meta = chunk.metadata_info or {}
        price = f" | Preço: R$ {meta.get('preco')}" if 'preco' in meta else ""
        chunks_text.append(f"• {chunk.content}{price}")

    return "\n".join(chunks_text)

