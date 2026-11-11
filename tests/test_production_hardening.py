from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.connection_monitor import connection_monitor
from app.embeddings import generate_semantic_embedding
from app.evolution_client import send_whatsapp_message
from app.main import app
from app.models import AuditLog, Base, Company, KnowledgeChunk
from app.queue_worker import (
    MAX_JOB_ATTEMPTS,
    QUEUE_DLQ,
    QUEUE_PENDING,
    QUEUE_PROCESSING,
    enqueue_webhook_event,
    process_next_job,
)
from tests.test_harness import TestingSessionLocal, fake_redis, test_engine


@pytest_asyncio.fixture(autouse=True)
async def ensure_tables_and_clean_state():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await fake_redis.flushall()
    yield


@pytest.mark.asyncio
async def test_webhook_and_admin_authentication_enforcement(monkeypatch):
    """
    Valida que WEBHOOK_SECRET e ADMIN_API_KEY bloqueiam acessos não autorizados (401)
    e aceitam credenciais válidas (200).
    """
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "super-webhook-secret-123")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "super-admin-key-456")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Webhook sem secret -> 401
        resp_no_auth = await client.post("/webhook/evolution", json={"event": "MESSAGES_UPSERT"})
        assert resp_no_auth.status_code == 401

        # 2. Webhook com secret errado -> 401
        resp_bad = await client.post(
            "/webhook/evolution",
            headers={"X-Webhook-Secret": "wrong-secret"},
            json={"event": "MESSAGES_UPSERT"},
        )
        assert resp_bad.status_code == 401

        # 3. Webhook com secret válido -> 200
        resp_ok = await client.post(
            "/webhook/evolution",
            headers={"X-Webhook-Secret": "super-webhook-secret-123"},
            json={"event": "MESSAGES_UPSERT", "data": {"key": {"fromMe": True}}},
        )
        assert resp_ok.status_code == 200

        # 4. Rotas administrativas sem chave -> 401
        resp_health_no_auth = await client.get("/api/instances/health")
        assert resp_health_no_auth.status_code == 401

        # 5. Rotas administrativas com Bearer válido -> 200
        resp_health_bearer = await client.get(
            "/api/instances/health",
            headers={"Authorization": "Bearer super-admin-key-456"},
        )
        assert resp_health_bearer.status_code == 200

        # 6. Rotas administrativas com X-Admin-Key válido -> 200
        resp_health_header = await client.get(
            "/api/instances/health",
            headers={"X-Admin-Key": "super-admin-key-456"},
        )
        assert resp_health_header.status_code == 200


@pytest.mark.asyncio
async def test_async_queue_ingestion_and_worker_execution(monkeypatch):
    """
    Valida o fluxo completo de produção em escala:
    API deduplica na borda e enfileira (<20ms, status='queued') -> Worker consome e processa.
    """
    monkeypatch.setattr(settings, "ASYNC_QUEUE_ENABLED", True)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "")

    async with TestingSessionLocal() as db:
        existing = (
            await db.execute(select(Company).where(Company.instance_name == "inst_queue_01"))
        ).scalar_one_or_none()
        if not existing:
            db.add(
                Company(
                    id="comp-queue-01",
                    owner_id="owner-q",
                    name="Pizzaria Fila Rápida",
                    instance_name="inst_queue_01",
                    segment="pizzaria_lanches",
                    phone_number="5511977770000",
                    active=True,
                )
            )
            await db.commit()

    webhook_payload = {
        "event": "MESSAGES_UPSERT",
        "instance": "inst_queue_01",
        "data": {
            "key": {
                "remoteJid": "5511988881234@s.whatsapp.net",
                "fromMe": False,
                "id": "MSG_QUEUE_TEST_001",
            },
            "pushName": "Carlos Fila",
            "message": {"conversation": "Olá"},
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Primeira entrega -> enfileirada
        resp = await client.post("/webhook/evolution", json=webhook_payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

        # 2. Duplicata -> descartada na borda antes da fila
        resp_dup = await client.post("/webhook/evolution", json=webhook_payload)
        assert resp_dup.status_code == 200
        assert resp_dup.json()["reason"] == "duplicate_event"

    assert await fake_redis.llen(QUEUE_PENDING) == 1

    # 3. Worker consome e processa o job usando mock no envio WhatsApp
    with patch("app.webhook_processor.send_whatsapp_message", new=AsyncMock(return_value=True)):
        job_res = await process_next_job(
            fake_redis,
            session_factory=TestingSessionLocal,
            blocking=False,
        )

    assert job_res is not None
    assert job_res["status"] == "ack"
    assert job_res["http_status"] == 200
    assert "Pizzaria Fila Rápida" in job_res["content"]["response"]

    # Filas pendente e em processamento devem estar vazias após ACK
    assert await fake_redis.llen(QUEUE_PENDING) == 0
    assert await fake_redis.llen(QUEUE_PROCESSING) == 0


@pytest.mark.asyncio
async def test_worker_dlq_after_max_retries():
    """
    Valida que jobs com erro persistente são re-enfileirados até MAX_JOB_ATTEMPTS
    e depois movidos para a Dead-Letter Queue (queue:webhooks:dlq).
    """
    bad_payload = {
        "event": "MESSAGES_UPSERT",
        "instance": "inst_queue_dlq",
        "data": {
            "key": {
                "remoteJid": "5511911112222@s.whatsapp.net",
                "fromMe": False,
                "id": "MSG_DLQ_001",
            },
            "message": {"conversation": "teste falha"},
        },
    }
    await enqueue_webhook_event(fake_redis, bad_payload)

    with patch("app.queue_worker.process_webhook_payload", side_effect=RuntimeError("Erro simulado de rede")):
        for attempt in range(1, MAX_JOB_ATTEMPTS):
            res = await process_next_job(fake_redis, session_factory=TestingSessionLocal, blocking=False)
            assert res["status"] == "requeued"
            assert res["attempts"] == attempt

        # Última tentativa deve mover para DLQ
        final_res = await process_next_job(fake_redis, session_factory=TestingSessionLocal, blocking=False)
        assert final_res["status"] == "dlq"
        assert final_res["attempts"] == MAX_JOB_ATTEMPTS

    assert await fake_redis.llen(QUEUE_PENDING) == 0
    assert await fake_redis.llen(QUEUE_DLQ) == 1


@pytest.mark.asyncio
async def test_evolution_client_exponential_backoff_retry():
    """
    Valida que send_whatsapp_message retenta falhas transitórias (503) com backoff exponencial
    e falha imediatamente em erros 4xx permanentes (400).
    """
    resp_503 = MagicMock(status_code=503, text="Service Unavailable")
    resp_201 = MagicMock(status_code=201, text="Created")
    resp_400 = MagicMock(status_code=400, text="Bad Request")

    # Cenário 1: 2 falhas 503 seguidas de sucesso 201
    mock_post_retry = AsyncMock(side_effect=[resp_503, resp_503, resp_201])
    with patch("httpx.AsyncClient.post", new=mock_post_retry):
        ok = await send_whatsapp_message(
            "inst_test", "5511999998888", "Olá!", max_retries=3, base_delay=0.01
        )
    assert ok is True
    assert mock_post_retry.call_count == 3

    # Cenário 2: Erro permanente 400 não deve retentar
    mock_post_400 = AsyncMock(return_value=resp_400)
    with patch("httpx.AsyncClient.post", new=mock_post_400):
        ok_400 = await send_whatsapp_message(
            "inst_test", "5511999998888", "Olá!", max_retries=3, base_delay=0.01
        )
    assert ok_400 is False
    assert mock_post_400.call_count == 1


@pytest.mark.asyncio
async def test_active_push_alert_and_redis_cooldown(monkeypatch):
    """
    Valida que alertas ativos de desconexão disparam webhook HTTP externo e respeitam
    o cooldown atômico no Redis para evitar spam durante oscilações.
    """
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "https://alerts.exemplo.local/hook")

    company = Company(
        id="comp-alert-01",
        owner_id="owner-alert",
        name="Lanchonete Alerta",
        instance_name="inst_alert_01",
        segment="lanchonete",
        active=True,
    )

    mock_post = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("httpx.AsyncClient.post", new=mock_post):
        # 1ª queda dispara alerta
        sent1 = await connection_monitor.dispatch_push_alert(
            company=company,
            instance_name="inst_alert_01",
            state="close",
            reason=408,
            alert_message="Queda detectada!",
            redis=fake_redis,
            cooldown_seconds=1800,
        )
        # 2ª queda imediata é suprimida pelo cooldown
        sent2 = await connection_monitor.dispatch_push_alert(
            company=company,
            instance_name="inst_alert_01",
            state="close",
            reason=408,
            alert_message="Queda detectada!",
            redis=fake_redis,
            cooldown_seconds=1800,
        )

    assert sent1 is True
    assert sent2 is False
    assert mock_post.call_count == 1


@pytest.mark.asyncio
async def test_pgvector_native_sql_compilation_and_embedding_fallback():
    """
    Valida que o tipo híbrido KnowledgeChunk.embedding compila operador nativo <=> no PostgreSQL
    e que generate_semantic_embedding fornece vetor 768-dim normalizado offline.
    """
    vec = await generate_semantic_embedding("Pizza Grande de Calabresa", api_key="mock-key")
    assert len(vec) == 768
    assert abs(sum(x * x for x in vec) - 1.0) < 1e-5

    dist_expr = KnowledgeChunk.embedding.cosine_distance(vec)
    compiled_sql = str(dist_expr.compile(dialect=postgresql.dialect()))
    assert "<=>" in compiled_sql
