import asyncio
import uuid
from sqlalchemy import select, text
from app.database import engine, AsyncSessionLocal, generate_mock_embedding
from app.models import Base, Company, KnowledgeChunk


async def seed_lanchonete():
    """Popula de forma idempotente o primeiro tenant de produção: Sabor & Forno Pizzaria e Lanches."""
    print("Iniciando seed do tenant de produção (Sabor & Forno Pizzaria e Lanches)...")

    async with AsyncSessionLocal() as db:
        # Verifica se o tenant já existe
        stmt = select(Company).where(Company.instance_name == "distribuidora_central")
        res = await db.execute(stmt)
        pizzaria = res.scalar_one_or_none()

        settings_pizzaria = {
            "horario_funcionamento": "Terça a Domingo, das 18h às 23h30 (Segunda-feira fechado)",
            "regras_entrega": "Tempo de entrega: 35 a 50 minutos (horários de pico até 60 min). Taxas: R$ 6,00 bairros centrais | R$ 10,00 a R$ 14,00 demais bairros.",
            "chave_pix": "PIX CNPJ: 12.345.678/0001-90 - Sabor & Forno",
            "politicas": "Cartão de Crédito/Débito (motoboy leva a maquininha) | Dinheiro (obrigatório informar se precisa de troco e para quanto).",
        }

        if not pizzaria:
            pizzaria = Company(
                id=str(uuid.uuid4()),
                owner_id="augusto",
                name="Sabor & Forno Pizzaria e Lanches",
                instance_name="distribuidora_central",
                segment="pizzaria_lanches",
                phone_number="5511988887777",
                settings=settings_pizzaria,
                active=True,
            )
            db.add(pizzaria)
            await db.flush()
            print("Empresa 'Sabor & Forno Pizzaria e Lanches' cadastrada com sucesso.")
        else:
            pizzaria.name = "Sabor & Forno Pizzaria e Lanches"
            pizzaria.segment = "pizzaria_lanches"
            pizzaria.settings = settings_pizzaria
            await db.flush()
            print("Empresa 'Sabor & Forno Pizzaria e Lanches' já existente. Configurações atualizadas.")

        # Limpa chunks antigos desse tenant para evitar duplicações
        del_stmt = text("DELETE FROM knowledge_chunks WHERE company_id = :cid")
        await db.execute(del_stmt, {"cid": pizzaria.id})

        # Itens do Cardápio RAG
        rag_items = [
            # 1. PIZZAS TRADICIONAIS
            {
                "content": "Pizzas Tradicionais - Tamanhos e Fatias: Média (6 fatias) R$ 42,00 | Grande (8 fatias) R$ 52,00 | Família (12 fatias) R$ 65,00.",
                "meta": {"categoria": "tamanhos", "precos": {"media": 42.0, "grande": 52.0, "familia": 65.0}},
            },
            {
                "content": "Pizza Tradicional Calabresa: Molho de tomate especial, mussarela, calabresa fatiada, cebola e orégano. Média R$ 42,00, Grande R$ 52,00, Família R$ 65,00.",
                "meta": {"categoria": "pizza_tradicional", "sabor": "Calabresa", "preco": 52.0},
            },
            {
                "content": "Pizza Tradicional Mussarela: Molho de tomate, camada generosa de mussarela, rodelas de tomate e orégano. Média R$ 42,00, Grande R$ 52,00, Família R$ 65,00.",
                "meta": {"categoria": "pizza_tradicional", "sabor": "Mussarela", "preco": 52.0},
            },
            {
                "content": "Pizza Tradicional Frango com Catupiry: Peito de frango desfiado temperado, mussarela e legítimo Catupiry. Média R$ 42,00, Grande R$ 52,00, Família R$ 65,00.",
                "meta": {"categoria": "pizza_tradicional", "sabor": "Frango com Catupiry", "preco": 52.0},
            },
            {
                "content": "Pizza Tradicional Portuguesa: Presunto cozido, mussarela, ovos, cebola, ervilha e azeitonas pretas. Média R$ 42,00, Grande R$ 52,00, Família R$ 65,00.",
                "meta": {"categoria": "pizza_tradicional", "sabor": "Portuguesa", "preco": 52.0},
            },
            {
                "content": "Pizza Tradicional Marguerita: Mussarela, rodelas de tomate fresco, manjericão fresco e azeite de oliva. Média R$ 42,00, Grande R$ 52,00, Família R$ 65,00.",
                "meta": {"categoria": "pizza_tradicional", "sabor": "Marguerita", "preco": 52.0},
            },
            {
                "content": "Pizza Tradicional Quatro Queijos: Mussarela, provolone, parmesão ralado e gorgonzola cremoso. Média R$ 42,00, Grande R$ 52,00, Família R$ 65,00.",
                "meta": {"categoria": "pizza_tradicional", "sabor": "Quatro Queijos", "preco": 52.0},
            },

            # 2. PIZZAS ESPECIAIS E DOCES
            {
                "content": "Pizza Especial Carne de Sol na Nata (Tamanho Grande 8 fatias): R$ 62,00. Carne de sol desfiada, nata fresca, mussarela e cebola roxa.",
                "meta": {"categoria": "pizza_especial", "sabor": "Carne de Sol na Nata", "preco": 62.0},
            },
            {
                "content": "Pizza Especial Bacon Crocante (Tamanho Grande 8 fatias): R$ 58,00. Mussarela, fatias crocantes de bacon e barbecue artesanal.",
                "meta": {"categoria": "pizza_especial", "sabor": "Bacon Crocante", "preco": 58.0},
            },
            {
                "content": "Pizza Doce Chocolate com Morango (Tamanho Média 6 fatias): R$ 45,00. Chocolate ao leite derretido e fatias de morango fresco.",
                "meta": {"categoria": "pizza_doce", "sabor": "Chocolate com Morango", "preco": 45.0},
            },
            {
                "content": "Pizza Doce Romeu e Julieta (Tamanho Média 6 fatias): R$ 40,00. Mussarela e goiabada cremosa.",
                "meta": {"categoria": "pizza_doce", "sabor": "Romeu e Julieta", "preco": 40.0},
            },

            # 3. SMASH BURGERS ARTESANAIS
            {
                "content": "Smash Burger Clássico: R$ 28,00. Pão brioche tostado na manteiga, 2x smash burger de 90g, queijo cheddar fatiado, picles e molho especial da casa. Acompanha batata frita.",
                "meta": {"categoria": "smash_burger", "lanche": "Smash Clássico", "preco": 28.0},
            },
            {
                "content": "Smash Bacon Burger: R$ 32,00. Pão brioche, 2x smash burger de 90g, queijo cheddar, fatias crocantes de bacon e cebola caramelizada. Acompanha batata rústica.",
                "meta": {"categoria": "smash_burger", "lanche": "Smash Bacon Burger", "preco": 32.0},
            },
            {
                "content": "Smash Frango Crispy: R$ 26,00. Pão brioche, sobrecoxa empanada super crocante, alface americana fresca e maionese verde artesanal.",
                "meta": {"categoria": "smash_burger", "lanche": "Smash Frango Crispy", "preco": 26.0},
            },

            # 4. BEBIDAS GELADAS
            {
                "content": "Refrigerante Lata 350ml: R$ 6,00. Opções: Coca-Cola original, Coca-Cola Zero, Guaraná Antarctica, Fanta Laranja.",
                "meta": {"categoria": "bebidas", "tipo": "refrigerante_lata", "preco": 6.0},
            },
            {
                "content": "Refrigerante 2 Litros: R$ 14,00. Opções: Coca-Cola Original, Guaraná Antarctica.",
                "meta": {"categoria": "bebidas", "tipo": "refrigerante_2l", "preco": 14.0},
            },
            {
                "content": "Sucos Naturais Del Valle 400ml: R$ 8,00. Sabores: Laranja, Uva, Maracujá.",
                "meta": {"categoria": "bebidas", "tipo": "suco", "preco": 8.0},
            },
            {
                "content": "Cervejas Long Neck 330ml: R$ 9,00. Opções: Heineken, Budweiser.",
                "meta": {"categoria": "bebidas", "tipo": "cerveja", "preco": 9.0},
            },
            {
                "content": "Água Mineral 500ml: R$ 4,00. Opções: com gás e sem gás.",
                "meta": {"categoria": "bebidas", "tipo": "agua", "preco": 4.0},
            },

            # 5. POLÍTICAS OPERACIONAIS
            {
                "content": "Horário de Funcionamento da Pizzaria: Terça a Domingo, das 18h às 23h30. Segunda-feira fechado.",
                "meta": {"categoria": "politicas", "tipo": "horario"},
            },
            {
                "content": "Tempo de Entrega e Taxas: Média de 35 a 50 minutos (em dias chuvosos ou pico até 60 min). Taxa de entrega R$ 6,00 para bairros centrais e R$ 10,00 a R$ 14,00 para demais regiões.",
                "meta": {"categoria": "politicas", "tipo": "entrega"},
            },
            {
                "content": "Formas de Pagamento: PIX (Chave CNPJ: 12.345.678/0001-90 - Sabor & Forno) | Cartão de Crédito/Débito na entrega (motoboy leva a maquininha) | Dinheiro (informar se precisa de troco e para quanto).",
                "meta": {"categoria": "politicas", "tipo": "pagamento"},
            },
        ]

        chunks_to_insert = [
            KnowledgeChunk(
                company_id=pizzaria.id,
                content=item["content"],
                metadata_info=item["meta"],
                embedding=generate_mock_embedding(item["content"]),
            )
            for item in rag_items
        ]

        db.add_all(chunks_to_insert)
        await db.commit()
        print(f"Seed concluído! {len(chunks_to_insert)} itens do catálogo RAG inseridos com sucesso.")


if __name__ == "__main__":
    asyncio.run(seed_lanchonete())
