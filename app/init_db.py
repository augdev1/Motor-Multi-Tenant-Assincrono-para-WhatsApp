import asyncio
import uuid
from sqlalchemy import text, select
from app.database import engine, AsyncSessionLocal, generate_mock_embedding
from app.models import Base, Company, KnowledgeChunk


async def init_database():
    """Cria tabelas no PostgreSQL e insere dados completos da Sabor & Forno Pizzaria e Lanches."""
    print("Iniciando configuração do banco de dados PostgreSQL...")

    async with engine.begin() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS evolution;"))
        except Exception as e:
            print(f"Nota ao criar extensões: {e}")

        await conn.run_sync(Base.metadata.create_all)
        print("Tabelas criadas com sucesso!")

    async with AsyncSessionLocal() as db:
        # Verifica se a empresa Sabor & Forno / distribuidora_central já existe
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
            print("Empresa 'Sabor & Forno Pizzaria e Lanches' criada.")
        else:
            pizzaria.name = "Sabor & Forno Pizzaria e Lanches"
            pizzaria.segment = "pizzaria_lanches"
            pizzaria.settings = settings_pizzaria
            await db.flush()
            print("Empresa 'Sabor & Forno Pizzaria e Lanches' atualizada.")

        # Limpa chunks antigos dessa empresa para recarregar a base atualizada
        del_stmt = text("DELETE FROM knowledge_chunks WHERE company_id = :cid")
        await db.execute(del_stmt, {"cid": pizzaria.id})

        # Base de Conhecimento RAG extraída de rag_pizzaria
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

            # 3. SANDUÍCHES E HAMBÚRGUERES ARTESANAIS
            {
                "content": "Smash Burguer Simples: R$ 18,00. Pão brioche, blend 90g smash, cheddar derretido e picles. Acompanha maionese especial da casa.",
                "meta": {"categoria": "hamburguer", "nome": "Smash Burguer Simples", "preco": 18.0},
            },
            {
                "content": "Smash Duplo Bacon: R$ 28,00. Pão brioche, 2 blends 90g smash, cheddar duplo, fatias de bacon e cebola caramelizada. Acompanha maionese especial da casa.",
                "meta": {"categoria": "hamburguer", "nome": "Smash Duplo Bacon", "preco": 28.0},
            },
            {
                "content": "X-Salada Artesanal: R$ 22,00. Pão tradicional, blend 150g, mussarela, alface americana, tomate e maionese verde da casa.",
                "meta": {"categoria": "hamburguer", "nome": "X-Salada Artesanal", "preco": 22.0},
            },
            {
                "content": "X-Tudo Monstro: R$ 32,00. Pão brioche, blend 160g, queijo, presunto, bacon crocante, ovo, alface, tomate e batata palha. Acompanha maionese especial da casa.",
                "meta": {"categoria": "hamburguer", "nome": "X-Tudo Monstro", "preco": 32.0},
            },

            # 4. BEBIDAS E REFRIGERANTES
            {
                "content": "Coca-Cola (Original ou Zero): Lata 350ml R$ 6,00 | Garrafa 2 Litros R$ 14,00.",
                "meta": {"categoria": "bebidas", "item": "Coca-Cola", "preco_lata": 6.0, "preco_2l": 14.0},
            },
            {
                "content": "Guaraná Antarctica: Lata 350ml R$ 6,00 | Garrafa 2 Litros R$ 13,00.",
                "meta": {"categoria": "bebidas", "item": "Guaraná Antarctica", "preco_lata": 6.0, "preco_2l": 13.0},
            },
            {
                "content": "Fanta Laranja ou Uva: Lata 350ml R$ 6,00.",
                "meta": {"categoria": "bebidas", "item": "Fanta", "preco": 6.0},
            },
            {
                "content": "Sucos Naturais (Laranja ou Maracujá): Copo 500ml R$ 9,00.",
                "meta": {"categoria": "bebidas", "item": "Suco Natural", "preco": 9.0},
            },
            {
                "content": "Água Mineral: Sem gás (500ml) R$ 4,00 | Com gás (500ml) R$ 5,00.",
                "meta": {"categoria": "bebidas", "item": "Água Mineral", "preco_sem_gas": 4.0, "preco_com_gas": 5.0},
            },

            # 5. POLÍTICAS, ENTREGA E PAGAMENTO
            {
                "content": "Horário de Funcionamento: Terça a Domingo, das 18h às 23h30 (Segunda-feira fechado). Tempo de Entrega: 35 a 50 minutos (pico até 60 min).",
                "meta": {"categoria": "operacional"},
            },
            {
                "content": "Taxas de Entrega: Bairros centrais R$ 6,00. Demais bairros atendidos: R$ 10,00 a R$ 14,00.",
                "meta": {"categoria": "taxa_entrega"},
            },
            {
                "content": "Formas de Pagamento: PIX (Chave CNPJ: 12.345.678/0001-90 - Sabor & Forno) | Cartão de Crédito/Débito na entrega | Dinheiro (informar troco).",
                "meta": {"categoria": "pagamento"},
            },
        ]

        chunks_to_add = []
        for item in rag_items:
            chunk = KnowledgeChunk(
                company_id=pizzaria.id,
                content=item["content"],
                metadata_info=item["meta"],
                embedding=generate_mock_embedding(item["content"]),
            )
            chunks_to_add.append(chunk)

        db.add_all(chunks_to_add)
        await db.commit()
        print(f"Base de Conhecimento RAG da Sabor & Forno ({len(chunks_to_add)} itens) inserida com sucesso!")


if __name__ == "__main__":
    asyncio.run(init_database())
