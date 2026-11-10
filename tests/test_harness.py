from datetime import datetime, timedelta, timezone
import uuid
from typing import AsyncGenerator
import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import generate_mock_embedding, get_db
from app.graph import ai_reasoning_node, build_bot_graph
from app.main import app, get_redis_client
from app.models import Base, Company, CustomerSession, KnowledgeChunk, MessageHistory, OrderRecord
from app.rag import build_system_prompt, get_chat_history, save_message, search_knowledge_base
from app.schemas import BotState


# 1. Configuração do SQLite in-memory compartilhado para testes determinísticos
test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# 2. FakeRedis in-memory compartilhado para os testes
fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Injeta a sessão SQLite in-memory."""
    async with TestingSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def override_get_redis():
    """Injeta o FakeRedis nos endpoints."""
    return fake_redis


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_redis_client] = override_get_redis


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """Cria o schema do banco de dados e popula os tenants e catálogos iniciais."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Popula dados de semente (Seed)
    async with TestingSessionLocal() as db:
        # Empresa A: Distribuidora de Bebidas e Gás
        empresa_a = Company(
            id="tenant-distribuidora-001",
            owner_id="augusto",
            name="Distribuidora Central de Bebidas e Gás",
            instance_name="distribuidora_central",
            segment="distribuidora",
            phone_number="5511988887777",
            active=True,
        )

        # Empresa B: Depósito de Materiais de Construção
        empresa_b = Company(
            id="tenant-deposito-002",
            owner_id="augusto",
            name="Depósito Esperança Materiais",
            instance_name="deposito_esperanca",
            segment="materiais_construcao",
            phone_number="5511977776666",
            active=True,
        )

        # Empresa C: Farmácia
        empresa_c = Company(
            id="tenant-farmacia-003",
            owner_id="outro_socio",
            name="Farmácia Vida e Saúde",
            instance_name="farmacia_vida",
            segment="farmacia",
            phone_number="5511966665555",
            active=True,
        )

        db.add_all([empresa_a, empresa_b, empresa_c])
        await db.flush()

        # Conhecimento RAG exclusivo da Empresa B (Materiais)
        chunk_cimento = KnowledgeChunk(
            id=str(uuid.uuid4()),
            company_id=empresa_b.id,
            content="Cimento Votoran Todas as Obras 50kg - Alta resistência e secagem rápida para fundações e reboco.",
            metadata_info={"preco": 34.90, "categoria": "cimento", "marca": "Votoran"},
            embedding=generate_mock_embedding("Cimento Votoran Todas as Obras 50kg"),
        )
        chunk_argamassa = KnowledgeChunk(
            id=str(uuid.uuid4()),
            company_id=empresa_b.id,
            content="Argamassa AC-III Cinza 20kg Quartzolit - Indicada para pisos cerâmicos e porcelanatos.",
            metadata_info={"preco": 28.50, "categoria": "argamassa", "marca": "Quartzolit"},
            embedding=generate_mock_embedding("Argamassa AC-III Cinza 20kg Quartzolit"),
        )
        chunk_tubo = KnowledgeChunk(
            id=str(uuid.uuid4()),
            company_id=empresa_b.id,
            content="Tubo PVC Esgoto 100mm barra com 6 metros Tigre - Alta durabilidade predial.",
            metadata_info={"preco": 56.00, "categoria": "hidraulica", "marca": "Tigre"},
            embedding=generate_mock_embedding("Tubo PVC Esgoto 100mm Tigre"),
        )

        # Conhecimento RAG exclusivo da Empresa C (Farmácia)
        chunk_farmacia = KnowledgeChunk(
            id=str(uuid.uuid4()),
            company_id=empresa_c.id,
            content="Dipirona Monoidratada 500mg com 10 comprimidos Medley - Analgésico e antitérmico para dor e febre.",
            metadata_info={"preco": 6.50, "categoria": "medicamento"},
            embedding=generate_mock_embedding("Dipirona Monoidratada 500mg Medley comprimidos"),
        )

        db.add_all([chunk_cimento, chunk_argamassa, chunk_tubo, chunk_farmacia])
        await db.commit()

    yield

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(autouse=True)
async def clear_redis():
    """Limpa o Redis in-memory entre os testes para isolamento completo."""
    await fake_redis.flushall()
    yield


@pytest_asyncio.fixture
async def client():
    """Cliente HTTP assíncrono conectado à aplicação FastAPI de teste."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def build_evolution_payload(
    instance_name: str,
    phone_number: str,
    message_text: str,
    message_id: str | None = None,
    customer_name: str = "Augusto Tester",
    from_me: bool = False,
    is_group: bool = False,
) -> dict:
    """Helper para construir payloads idênticos aos emitidos pela Evolution API v2."""
    msg_id = message_id or f"EVO_MSG_{uuid.uuid4().hex[:12]}"
    remote_jid = f"{phone_number}@g.us" if is_group else f"{phone_number}@s.whatsapp.net"
    return {
        "event": "messages.upsert",
        "instance": instance_name,
        "data": {
            "key": {
                "remoteJid": remote_jid,
                "fromMe": from_me,
                "id": msg_id,
            },
            "pushName": customer_name,
            "message": {
                "conversation": message_text,
            },
        },
    }


# ==============================================================================
# SUÍTE DE TESTES OBRIGATÓRIOS DO TEST HARNESS
# ==============================================================================

@pytest.mark.asyncio
async def test_tenant_resolution_and_isolation(client: AsyncClient):
    """
    1. test_tenant_resolution_and_isolation:
       Garante que dados da Empresa A não se misturam aos da Empresa B.
       Valida que cada instância aciona seu respectivo tenant e que sessões e
       consultas RAG permanecem estritamente isoladas por company_id.
    """
    # Consulta na Empresa B (Materiais) procurando por Cimento
    payload_b = build_evolution_payload(
        instance_name="deposito_esperanca",
        phone_number="5511999990001",
        message_text="Qual o preço do cimento?",
    )
    resp_b = await client.post("/webhook/evolution", json=payload_b)
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    assert data_b["status"] == "processed"
    assert "Depósito Esperança Materiais" in data_b["response"]
    assert "Cimento Votoran" in data_b["response"]
    assert "R$ 34.9" in data_b["response"]

    # Consulta na Empresa A (Distribuidora) com o mesmo usuário
    payload_a = build_evolution_payload(
        instance_name="distribuidora_central",
        phone_number="5511999990001",
        message_text="2",  # Opção ver preços da distribuidora
    )
    resp_a = await client.post("/webhook/evolution", json=payload_a)
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["status"] == "processed"
    assert "Distribuidora Central de Bebidas e Gás" in data_a["response"]
    assert "Água Mineral 20L" in data_a["response"]
    # Não pode vazar nada do Depósito Esperança
    assert "Cimento" not in data_a["response"]


@pytest.mark.asyncio
async def test_delivery_order_full_lifecycle(client: AsyncClient):
    """
    2. test_delivery_order_full_lifecycle:
       Simulação ponta a ponta de um pedido completo até a emissão da comanda.
       Menu -> Coleta de Itens -> Coleta de Endereço -> Coleta de Pagamento -> Comanda formatada.
    """
    customer_phone = "5511987654321"

    # Etapa 1: Início - Seleciona Fazer Pedido (Opção 1)
    p1 = build_evolution_payload("distribuidora_central", customer_phone, "1")
    r1 = await client.post("/webhook/evolution", json=p1)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["current_step"] == "coletando_itens"
    assert "digite os itens" in d1["response"]

    # Etapa 2: Coleta de Itens
    p2 = build_evolution_payload("distribuidora_central", customer_phone, "2 galões de água 20L e 1 gás P13")
    r2 = await client.post("/webhook/evolution", json=p2)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["current_step"] == "coletando_endereco"
    assert "endereço completo" in d2["response"]

    # Etapa 3: Coleta de Endereço
    p3 = build_evolution_payload("distribuidora_central", customer_phone, "Av. Paulista, 1000, Apto 52 - Bela Vista")
    r3 = await client.post("/webhook/evolution", json=p3)
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3["current_step"] == "coletando_pagamento"
    assert "forma de pagamento" in d3["response"]

    # Etapa 4: Coleta de Pagamento e Emissão da Comanda Formatada
    p4 = build_evolution_payload("distribuidora_central", customer_phone, "Pix na entrega")
    r4 = await client.post("/webhook/evolution", json=p4)
    assert r4.status_code == 200
    d4 = r4.json()
    assert d4["current_step"] == "inicio"
    assert d4["order_id"] is not None

    # Validação rigorosa da comanda formatada
    response_text = d4["response"]
    assert "COMANDA DE EXPEDIÇÃO - PEDIDO CONFIRMADO" in response_text
    assert "Distribuidora Central de Bebidas e Gás" in response_text
    assert "Av. Paulista, 1000" in response_text
    assert "Pix na entrega" in response_text
    assert "2 galões de água 20L e 1 gás P13" in response_text
    assert "Recebido e em separação para entrega" in response_text


@pytest.mark.asyncio
async def test_human_handoff_trigger(client: AsyncClient):
    """
    3. test_human_handoff_trigger:
       Valida a transferência imediata para atendimento humano (human_handoff = True)
       na opção 3 ou ao solicitar atendente.
    """
    payload = build_evolution_payload(
        instance_name="distribuidora_central",
        phone_number="5511999990003",
        message_text="3",
    )
    resp = await client.post("/webhook/evolution", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processed"
    assert data["human_handoff"] is True
    assert "atendentes humanos" in data["response"]


@pytest.mark.asyncio
async def test_redis_deduplication(client: AsyncClient):
    """
    4. test_redis_deduplication:
       Valida descarte de mensagens com message_id repetido via SET NX EX.
    """
    fixed_msg_id = "EVO_DEDUP_MSG_TEST_999"
    payload = build_evolution_payload(
        instance_name="distribuidora_central",
        phone_number="5511999990004",
        message_text="Olá, quero ver o menu",
        message_id=fixed_msg_id,
    )

    # Primeira execução: deve processar com sucesso
    resp1 = await client.post("/webhook/evolution", json=payload)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "processed"

    # Segunda execução idêntica (mesmo message_id): deve ser ignorada por deduplicação
    resp2 = await client.post("/webhook/evolution", json=payload)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "ignored"
    assert data2["reason"] == "duplicate_event"


@pytest.mark.asyncio
async def test_lock_contention(client: AsyncClient):
    """
    5. test_lock_contention:
       Valida descarte gracioso em concorrência simultânea via DistributedLock.
       Simula um lock já ativo para lock:company:{company_id}:user:{phone_number}.
       Em colisão, deve responder { "status": "busy", "reason": "lock_contention" } (HTTP 429).
    """
    phone_number = "5511999990005"
    company_id = "tenant-distribuidora-001"
    lock_key = f"lock:company:{company_id}:user:{phone_number}"

    # Simula lock já retido por outro processo
    await fake_redis.set(lock_key, "token_externo_ativo", nx=True, ex=10)

    payload = build_evolution_payload(
        instance_name="distribuidora_central",
        phone_number=phone_number,
        message_text="Oi, mensagem simultânea",
    )

    response = await client.post("/webhook/evolution", json=payload)
    assert response.status_code == 429
    data = response.json()
    assert data["status"] == "busy"
    assert data["reason"] == "lock_contention"

    # Libera o lock e valida que uma nova requisição é aceita normalmente
    await fake_redis.delete(lock_key)
    response_retry = await client.post("/webhook/evolution", json=payload)
    assert response_retry.status_code == 200
    assert response_retry.json()["status"] == "processed"


@pytest.mark.asyncio
async def test_material_construcao_rag_response(client: AsyncClient):
    """
    6. test_material_construcao_rag_response:
       Valida injeção de catálogo via RAG vetorial e resposta estruturada no segmento de materiais de construção.
       Também valida transbordo automático se a solicitação técnica não constar no catálogo.
    """
    # 1. Pergunta por produto existente no catálogo (Argamassa)
    payload_rag = build_evolution_payload(
        instance_name="deposito_esperanca",
        phone_number="5511999990006",
        message_text="Vocês têm argamassa quartzolit?",
    )
    resp_rag = await client.post("/webhook/evolution", json=payload_rag)
    assert resp_rag.status_code == 200
    data_rag = resp_rag.json()
    assert data_rag["status"] == "processed"
    assert data_rag["human_handoff"] is False
    assert "Argamassa AC-III Cinza 20kg Quartzolit" in data_rag["response"]
    assert "R$ 28.5" in data_rag["response"]

    # 2. Pergunta por solicitação técnica inexistente no catálogo -> Fallback com human_handoff = True
    payload_fallback = build_evolution_payload(
        instance_name="deposito_esperanca",
        phone_number="5511999990006",
        message_text="Preciso de um guindaste telescópico industrial para locação por 15 dias",
    )
    resp_fallback = await client.post("/webhook/evolution", json=payload_fallback)
    assert resp_fallback.status_code == 200
    data_fallback = resp_fallback.json()
    assert data_fallback["status"] == "processed"
    assert data_fallback["human_handoff"] is True
    assert "transferindo sua solicitação diretamente para o nosso especialista no balcão" in data_fallback["response"]


@pytest.mark.asyncio
async def test_outbound_and_group_filtering(client: AsyncClient):
    """
    Testa filtros essenciais de WhatsApp:
    1. Mensagens emitidas pelo próprio bot (fromMe == True) devem ser ignoradas.
    2. Mensagens originadas de grupos (@g.us) devem ser ignoradas.
    """
    # 1. Mensagem fromMe
    payload_from_me = build_evolution_payload(
        instance_name="distribuidora_central",
        phone_number="5511999990007",
        message_text="Mensagem enviada pelo próprio número do bot",
        from_me=True,
    )
    resp1 = await client.post("/webhook/evolution", json=payload_from_me)
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "ignored"
    assert resp1.json()["reason"] == "outbound_or_group"

    # 2. Mensagem de grupo (@g.us)
    payload_group = build_evolution_payload(
        instance_name="distribuidora_central",
        phone_number="120363024888888888",
        message_text="Mensagem em grupo familiar",
        is_group=True,
    )
    resp2 = await client.post("/webhook/evolution", json=payload_group)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"
    assert resp2.json()["reason"] == "outbound_or_group"


@pytest.mark.asyncio
async def test_pharmacy_rag_search(client: AsyncClient):
    """
    Testa consulta no nicho de Farmácia (Empresa C), validando resposta estruturada de catálogo.
    """
    payload_farm = build_evolution_payload(
        instance_name="farmacia_vida",
        phone_number="5511999990008",
        message_text="Vocês têm dipirona?",
    )
    resp = await client.post("/webhook/evolution", json=payload_farm)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processed"
    assert "Dipirona Monoidratada 500mg" in data["response"]
    assert "R$ 6.5" in data["response"]
    assert "Farmácia Vida e Saúde" in data["response"]


# ==============================================================================
# NOVOS TESTES DA CAMADA COGNITIVA, MEMÓRIA E RAG DINÂMICO
# ==============================================================================

@pytest.mark.asyncio
async def test_sliding_window_memory():
    """
    1. test_sliding_window_memory:
       Garante que apenas as últimas N mensagens são recuperadas da tabela
       message_history e que respeitam estritamente a ordem cronológica crescente.
    """
    company_id = "tenant-deposito-002"
    phone_number = "5511988880001"
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    async with TestingSessionLocal() as session:
        # Insere 10 mensagens com espaçamento temporal estrito
        for i in range(1, 11):
            role = "user" if i % 2 != 0 else "assistant"
            content = f"Mensagem teste {i}"
            msg = MessageHistory(
                company_id=company_id,
                customer_phone=phone_number,
                role=role,
                content=content,
                created_at=base_time + timedelta(seconds=i * 10),
            )
            session.add(msg)
        await session.commit()

        # Recupera com janela padrão limit=6
        history = await get_chat_history(session, company_id, phone_number, limit=6)
        assert len(history) == 6

        # Valida que as mensagens retornadas são as 6 últimas (5 a 10) na ordem cronológica crescente
        expected_contents = [f"Mensagem teste {i}" for i in range(5, 11)]
        actual_contents = [msg.content for msg in history]
        assert actual_contents == expected_contents

        # Valida tipagem das mensagens do LangChain
        assert isinstance(history[0], HumanMessage)  # 5 é ímpar -> user -> HumanMessage
        assert isinstance(history[1], AIMessage)


@pytest.mark.asyncio
async def test_receipt_vision_audit():
    """
    5. test_receipt_vision_audit:
       Valida a recepção de imagem de comprovante Pix via webhook, extraindo o nome
       do titular da conta bancária pagadora (Maria Souza) com Gemini Vision e gerando
       a confirmação transparente para o cliente.
    """
    client_phone = "5511999993333"
    instance_name = "distribuidora_central"

    # Prepara payload simulando imagem de comprovante com base64 inline
    dummy_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    payload = {
        "event": "messages.upsert",
        "instance": instance_name,
        "data": {
            "key": {
                "remoteJid": f"{client_phone}@s.whatsapp.net",
                "fromMe": False,
                "id": f"RECEIPT-{uuid.uuid4()}",
            },
            "pushName": "Eduardo Souza",
            "message": {
                "imageMessage": {
                    "caption": "Segue o comprovante do Pix!",
                    "base64": dummy_b64,
                }
            },
        },
    }

    from unittest.mock import patch

    mock_receipt = {
        "is_receipt": True,
        "pagador": "Maria Souza",
        "valor": 66.00,
        "destinatario": "Sabor & Forno Pizzaria e Lanches",
        "data_hora": "10/11/2026 - 19:08:30",
        "id_transacao": "E12345678MOCK",
    }

    transport = ASGITransport(app=app)
    with patch("app.main.extract_receipt_info", return_value=mock_receipt):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/webhook/evolution", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("status") == "processed"
            assert data.get("receipt_verified") is True
            assert data.get("pagador_pix") == "Maria Souza"
            assert "Maria Souza" in data.get("response", "")
            assert "Titular da Conta Bancária: Maria Souza" in data.get("response", "")
    # 6 é par -> assistant -> AIMessage


@pytest.mark.asyncio
async def test_memory_tenant_isolation():
    """
    2. test_memory_tenant_isolation:
       Garante isolamento multi-tenant estrito da memória: o histórico do telefone X
       na Empresa A não aparece quando o mesmo número interage com a Empresa B.
    """
    same_phone = "5511988880002"
    tenant_a = "tenant-distribuidora-001"
    tenant_b = "tenant-deposito-002"

    async with TestingSessionLocal() as session:
        # Registra mensagens do cliente na Empresa A
        await save_message(session, tenant_a, same_phone, "user", "Gostaria de pedir 3 galões de água mineral")
        await save_message(session, tenant_a, same_phone, "assistant", "Pedido de água registrado!")

        # Registra mensagens do mesmo cliente na Empresa B
        await save_message(session, tenant_b, same_phone, "user", "Qual o valor do cimento Votoran?")
        await save_message(session, tenant_b, same_phone, "assistant", "O cimento está saindo por R$ 34,90.")
        await session.commit()

        # Consulta histórico para a Empresa A
        history_a = await get_chat_history(session, tenant_a, same_phone, limit=10)
        assert len(history_a) == 2
        assert all("água" in msg.content or "Pedido" in msg.content for msg in history_a)
        assert not any("cimento" in msg.content for msg in history_a)

        # Consulta histórico para a Empresa B
        history_b = await get_chat_history(session, tenant_b, same_phone, limit=10)
        assert len(history_b) == 2
        assert all("cimento" in msg.content for msg in history_b)
        assert not any("água" in msg.content for msg in history_b)


@pytest.mark.asyncio
async def test_dynamic_context_injection():
    """
    3. test_dynamic_context_injection:
       Valida a montagem dinâmica do System Prompt incorporando a identidade da loja,
       diretrizes de Company.settings, contexto RAG e instruções anti-alucinação.
    """
    async with TestingSessionLocal() as session:
        # Configura empresa com diretrizes ricas
        company = Company(
            id=str(uuid.uuid4()),
            owner_id="augusto",
            name="Depósito Esperança Materiais",
            instance_name="deposito_esperanca_prompt_test",
            segment="materiais_construcao",
            phone_number="5511977770003",
            settings={
                "horario_funcionamento": "Segunda a Sexta das 07:00 às 17:00",
                "regras_entrega": "Frete grátis para compras acima de R$ 500,00 na Grande SP.",
                "chave_pix": "financeiro@depositoesperanca.com.br",
                "politicas": "Trocas de pisos e revestimentos apenas com embalagem intacta.",
            },
            active=True,
        )
        session.add(company)
        await session.commit()

        # Simula contexto RAG de catálogo
        rag_context = "• Cimento Votoran Todas as Obras 50kg - Preço: R$ 34.90"
        system_prompt = build_system_prompt(company, rag_context)

        # Asserções de conteúdo obrigatório no prompt
        assert 'empresa "Depósito Esperança Materiais"' in system_prompt
        assert "materiais_construcao" in system_prompt
        assert "Segunda a Sexta das 07:00 às 17:00" in system_prompt
        assert "Frete grátis para compras acima de R$ 500,00" in system_prompt
        assert "financeiro@depositoesperanca.com.br" in system_prompt
        assert "Cimento Votoran Todas as Obras 50kg" in system_prompt
        assert "R$ 34.90" in system_prompt
        assert "PROIBIÇÃO RIGOROSA: Nunca invente, alucine ou deduza preços" in system_prompt


@pytest.mark.asyncio
async def test_ai_reasoning_node_mocked():
    """
    4. test_ai_reasoning_node_mocked:
       Valida o nó de inferência cognitiva (ai_reasoning_node) utilizando
       FakeListChatModel e validando o uso de memória conversacional persistida.
    """
    phone_number = "5511999990009"
    company_id = "tenant-deposito-002"

    async with TestingSessionLocal() as session:
        # Mock de respostas do modelo
        expected_response = (
            "Olá! O Cimento Votoran 50kg está saindo por *R$ 34,90* a unidade. "
            "Quantos sacos você vai precisar para sua obra?"
        )
        fake_llm = FakeListChatModel(responses=[expected_response])

        # Estado inicial
        state = BotState(
            company_id=company_id,
            instance_name="deposito_esperanca",
            phone_number=phone_number,
            user_input="Qual o valor do cimento?",
        )

        config = {
            "configurable": {
                "db": session,
                "llm": fake_llm,
            }
        }

        # Execução do nó cognitivo
        result = await ai_reasoning_node(state, config)
        await session.commit()

        # Validação do resultado da inferência
        assert result["bot_response"] == expected_response
        assert result["human_handoff"] is False
        assert "Cimento Votoran" in (result["rag_context"] or "")

        # Valida que as mensagens foram persistidas no MessageHistory
        history = await get_chat_history(session, company_id, phone_number, limit=6)
        assert len(history) == 2
        assert history[0].content == "Qual o valor do cimento?"
        assert isinstance(history[0], HumanMessage)
        assert history[1].content == expected_response
        assert isinstance(history[1], AIMessage)


@pytest.mark.asyncio
async def test_receipt_vision_audit():
    """
    5. test_receipt_vision_audit:
       Valida a recepção de imagem de comprovante Pix via webhook, extraindo o nome
       do titular da conta bancária pagadora (Maria Souza) com Gemini Vision e gerando
       a confirmação transparente para o cliente.
    """
    client_phone = "5511999993333"
    instance_name = "distribuidora_central"

    # Prepara payload simulando imagem de comprovante com base64 inline
    dummy_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    payload = {
        "event": "messages.upsert",
        "instance": instance_name,
        "data": {
            "key": {
                "remoteJid": f"{client_phone}@s.whatsapp.net",
                "fromMe": False,
                "id": f"RECEIPT-{uuid.uuid4()}",
            },
            "pushName": "Eduardo Souza",
            "message": {
                "imageMessage": {
                    "caption": "Segue o comprovante do Pix!",
                    "base64": dummy_b64,
                }
            },
        },
    }

    from unittest.mock import patch

    mock_receipt = {
        "is_receipt": True,
        "pagador": "Maria Souza",
        "valor": 66.00,
        "destinatario": "Sabor & Forno Pizzaria e Lanches",
        "data_hora": "10/11/2026 - 19:08:30",
        "id_transacao": "E12345678MOCK",
    }

    transport = ASGITransport(app=app)
    with patch("app.main.extract_receipt_info", return_value=mock_receipt):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/webhook/evolution", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("status") == "processed"
            assert data.get("receipt_verified") is True
            assert data.get("pagador_pix") == "Maria Souza"
            assert "Maria Souza" in data.get("response", "")
            assert "Titular da Conta Bancária: Maria Souza" in data.get("response", "")



