import os
from datetime import datetime, timezone
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.connection_monitor import connection_monitor
from app.main import app
from app.models import AuditLog, Base, Company
from tests.test_harness import TestingSessionLocal, fake_redis, test_engine


@pytest_asyncio.fixture(autouse=True)
async def ensure_db_tables():
    """Garante que as tabelas estejam criadas no banco em memória antes de cada teste."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await fake_redis.flushall()
    yield


@pytest.mark.asyncio
async def test_connection_update_close_triggers_critical_alert():
    """
    Testa se a queda da conexão (CONNECTION_UPDATE com state='close')
    registra o log de auditoria crítico, persiste o alerta e marca disconnected_at.
    """
    async with TestingSessionLocal() as db:
        company = Company(
            id="comp-monitor-01",
            owner_id="owner-01",
            name="Pizzaria Teste Queda",
            instance_name="instancia_pizzaria_01",
            segment="pizzaria",
            phone_number="5511999991111",
            settings={"connection_status": "open"},
            active=True,
        )
        db.add(company)
        await db.commit()

    payload_dict = {
        "event": "CONNECTION_UPDATE",
        "instance": "instancia_pizzaria_01",
        "data": {
            "instance": "instancia_pizzaria_01",
            "state": "close",
            "statusReason": 408,
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/evolution", json=payload_dict)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processed"
        assert data["event"] == "CONNECTION_UPDATE"
        assert data["state"] == "close"
        assert data["is_connected"] is False
        assert data["alert_triggered"] is True
        assert "CAIU" in data["alert_message"]

    # Validação no banco de dados
    async with TestingSessionLocal() as db:
        comp = (await db.execute(select(Company).where(Company.id == "comp-monitor-01"))).scalar_one()
        assert comp.settings["connection_status"] == "close"
        assert comp.settings["connection_reason"] == 408
        assert "disconnected_at" in comp.settings

        # Verifica criação do registro de auditoria crítico
        audit = (await db.execute(
            select(AuditLog).where(AuditLog.event_type == "WHATSAPP_DISCONNECTED_ALERT")
        )).scalar_one_or_none()
        assert audit is not None
        assert audit.payload["severity"] == "CRITICAL"
        assert audit.payload["current_state"] == "close"

    # Validação do cache Redis
    status_cached = await fake_redis.get("evolution:status:instancia_pizzaria_01")
    assert status_cached == "close"
    alert_cached = await fake_redis.get("evolution:alert:instancia_pizzaria_01")
    assert "ALERTA CRÍTICO" in alert_cached


@pytest.mark.asyncio
async def test_connection_update_open_restores_health():
    """
    Testa se a restauração da conexão (CONNECTION_UPDATE com state='open')
    limpa o status de queda, limpa a flag de desconexão e remove o alerta do Redis.
    """
    async with TestingSessionLocal() as db:
        company = Company(
            id="comp-monitor-02",
            owner_id="owner-02",
            name="Hamburgueria Recuperada",
            instance_name="instancia_burguer_02",
            segment="lanches",
            phone_number="5511999992222",
            settings={
                "connection_status": "close",
                "disconnected_at": "2026-11-10T12:00:00+00:00"
            },
            active=True,
        )
        db.add(company)
        await db.commit()

    await fake_redis.set("evolution:status:instancia_burguer_02", "close")
    await fake_redis.set("evolution:alert:instancia_burguer_02", "Alerta antigo")

    payload_dict = {
        "event": "connection.update",
        "instance": "instancia_burguer_02",
        "data": {
            "instance": "instancia_burguer_02",
            "state": "open",
            "statusReason": 200,
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/evolution", json=payload_dict)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processed"
        assert data["state"] == "open"
        assert data["is_connected"] is True
        assert data["alert_triggered"] is False

    # Validação no banco de dados
    async with TestingSessionLocal() as db:
        comp = (await db.execute(select(Company).where(Company.id == "comp-monitor-02"))).scalar_one()
        assert comp.settings["connection_status"] == "open"
        assert "disconnected_at" not in comp.settings
        assert "last_connected_at" in comp.settings

        audit = (await db.execute(
            select(AuditLog).where(AuditLog.event_type == "WHATSAPP_CONNECTED")
        )).scalar_one_or_none()
        assert audit is not None
        assert audit.payload["state"] == "open"

    # Validação da limpeza no Redis
    status_cached = await fake_redis.get("evolution:status:instancia_burguer_02")
    assert status_cached == "open"
    alert_cached = await fake_redis.get("evolution:alert:instancia_burguer_02")
    assert alert_cached is None


@pytest.mark.asyncio
async def test_api_connection_status_and_health_endpoints():
    """
    Testa os endpoints GET /api/companies/{id}/connection-status e /api/instances/health.
    """
    async with TestingSessionLocal() as db:
        c1 = Company(
            id="comp-h-01",
            owner_id="ow1",
            name="Loja Online",
            instance_name="inst_online",
            segment="geral",
            settings={"connection_status": "open", "last_connection_update": datetime.now(timezone.utc).isoformat()},
            active=True,
        )
        c2 = Company(
            id="comp-h-02",
            owner_id="ow2",
            name="Loja Offline",
            instance_name="inst_offline",
            segment="geral",
            settings={
                "connection_status": "close",
                "disconnected_at": datetime.now(timezone.utc).isoformat(),
                "last_connection_update": datetime.now(timezone.utc).isoformat()
            },
            active=True,
        )
        db.add_all([c1, c2])
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Endpoint individual de status
        resp_c1 = await client.get("/api/companies/comp-h-01/connection-status")
        assert resp_c1.status_code == 200
        d1 = resp_c1.json()
        assert d1["is_connected"] is True
        assert d1["connection_status"] == "open"
        assert d1["alert_required"] is False

        resp_c2 = await client.get("/api/companies/inst_offline/connection-status")
        assert resp_c2.status_code == 200
        d2 = resp_c2.json()
        assert d2["is_connected"] is False
        assert d2["connection_status"] == "close"
        assert d2["alert_required"] is True

        # Endpoint global de saúde
        resp_health = await client.get("/api/instances/health")
        assert resp_health.status_code == 200
        h_data = resp_health.json()
        assert h_data["total_instances"] >= 2
        assert h_data["offline_instances"] >= 1
        assert h_data["requires_attention"] is True
        assert any(inst["instance_name"] == "inst_offline" for inst in h_data["instances"])
        assert any(inst["instance_name"] == "inst_online" for inst in h_data["instances"])


def test_docker_compose_files_have_restart_always_and_session_volumes():
    """
    Valida estaticamente se os arquivos docker-compose contêm
    restart: always e os volumes persistentes de instâncias e store da Evolution API.
    """
    compose_files = ["docker-compose.yml", "docker-compose.prod.yml", "docker-compose.ip-test.yml"]
    for file_name in compose_files:
        assert os.path.exists(file_name), f"Arquivo {file_name} não encontrado."
        with open(file_name, "r", encoding="utf-8") as f:
            content = f.read()

        # Verifica restart: always
        assert "restart: always" in content, f"{file_name} não possui restart: always."

        # Verifica mapeamento do volume de dados da sessão
        assert "/evolution/instances" in content, f"{file_name} não mapeia /evolution/instances."
        assert "/evolution/store" in content, f"{file_name} não mapeia /evolution/store."
