from contextlib import asynccontextmanager
from typing import Optional

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.connection_monitor import connection_monitor
from app.database import EventDeduplicator, get_db
from app.evolution_client import fetch_evolution_media_base64, send_whatsapp_message
from app.graph import build_bot_graph
from app.logging_config import log_audit_event
from app.models import AuditLog, Company
from app.queue_worker import enqueue_webhook_event
from app.schemas import EvolutionMessagePayload
from app.security import verify_admin_api_key, verify_webhook_secret
from app.vision import extract_receipt_info
from app.webhook_processor import process_webhook_payload

# Cliente Redis global para produção
redis_client: Optional[aioredis.Redis] = None
bot_graph = build_bot_graph()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia ciclo de vida dos recursos compartilhados (Redis, Graph, etc.)."""
    global redis_client
    try:
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception:
        # Permite subir mesmo se o Redis externo não estiver de pé imediatamente
        pass

    yield

    if redis_client:
        await redis_client.aclose()


app = FastAPI(
    title="Motor Multi-Tenant WhatsApp",
    description="Microsserviço assíncrono para automação de atendimento, catálogos (RAG) e fechamento de pedidos.",
    version="0.2.0",
    lifespan=lifespan,
)


def get_redis_client():
    """Dependency injection para o cliente Redis."""
    global redis_client
    return redis_client


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "whatsapp-multi-tenant-engine"}


@app.post("/webhook/evolution", dependencies=[Depends(verify_webhook_secret)])
async def evolution_webhook(
    payload: EvolutionMessagePayload,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis_client),
):
    """
    Endpoint principal para recebimento de webhooks da Evolution API v2.
    Em modo assíncrono (ASYNC_QUEUE_ENABLED=True), valida, deduplica e enfileira no Redis em <20ms.
    Em modo inline, orquestra diretamente o pipeline completo de resposta.
    """
    # 0. Monitoramento de Queda e Conectividade do WhatsApp (CONNECTION_UPDATE)
    if payload.is_connection_update():
        return await connection_monitor.handle_connection_update(payload, db, redis)

    # 1. Filtro de mensagens emitidas pelo bot e de grupos do WhatsApp
    if payload.is_from_me() or payload.is_group():
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": "outbound_or_group"}
        )

    instance_name = payload.get_instance_name()
    phone_number = payload.get_phone_number()
    message_id = payload.get_message_id()

    log_audit_event(
        event_type="INBOUND_WEBHOOK_RECEIVED",
        phone_number=phone_number,
        details={"instance": instance_name, "message_id": message_id, "is_media": payload.is_image_or_document()}
    )

    if not instance_name or not phone_number:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": "missing_instance_or_phone"}
        )

    # 2. Modo de Escala Assíncrona: Deduplica na borda e publica na fila Redis (<20ms)
    if settings.ASYNC_QUEUE_ENABLED and redis:
        if message_id:
            deduplicator = EventDeduplicator(redis)
            is_new = await deduplicator.check_and_set(message_id, ttl_seconds=300)
            if not is_new:
                log_audit_event(
                    event_type="EVENT_DEDUPLICATED",
                    phone_number=phone_number,
                    details={"message_id": message_id, "action": "ignored"}
                )
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={"status": "ignored", "reason": "duplicate_event"}
                )

        await enqueue_webhook_event(redis, payload.model_dump())
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "queued", "message_id": message_id, "instance": instance_name}
        )

    # 3. Modo Inline Direto (testes ou execução single-process)
    status_code, content = await process_webhook_payload(
        payload=payload,
        db=db,
        redis=redis,
        bot_graph=bot_graph,
        skip_dedup=False,
        await_send=False,
        extract_receipt_fn=extract_receipt_info,
    )
    return JSONResponse(status_code=status_code, content=content)


@app.get(
    "/audit/logs/{company_id_or_instance}",
    dependencies=[Depends(verify_admin_api_key)],
)
async def get_audit_trail(
    company_id_or_instance: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """
    Recupera os registros de auditoria filtrados estritamente pelo tenant.
    Permite busca por UUID da empresa ou instance_name da Evolution API.
    """
    stmt_comp = select(Company).where(
        (Company.id == company_id_or_instance) | (Company.instance_name == company_id_or_instance)
    )
    res_comp = await db.execute(stmt_comp)
    company = res_comp.scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail="Empresa não localizada.")

    stmt = (
        select(AuditLog)
        .where(AuditLog.company_id == company.id)
        .order_by(AuditLog.created_at.desc())
        .limit(min(limit, 200))
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    return {
        "company_id": company.id,
        "company_name": company.name,
        "total_returned": len(logs),
        "logs": [
            {
                "id": l.id,
                "event_type": l.event_type,
                "phone_number": l.phone_number,
                "payload": l.payload,
                "execution_time_ms": l.execution_time_ms,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ]
    }


@app.get(
    "/api/companies/{company_id_or_instance}/connection-status",
    dependencies=[Depends(verify_admin_api_key)],
)
async def get_company_connection_status(
    company_id_or_instance: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna o status de conexão em tempo real da instância WhatsApp da empresa.
    Informa se está conectado (open), desconectado (close/refused), tempo de queda e se requer alerta.
    """
    stmt = select(Company).where(
        (Company.id == company_id_or_instance) | (Company.instance_name == company_id_or_instance)
    )
    res = await db.execute(stmt)
    company = res.scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail="Empresa não localizada.")

    return connection_monitor.get_status_summary(company)


@app.get(
    "/api/instances/health",
    dependencies=[Depends(verify_admin_api_key)],
)
async def get_all_instances_health(
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna o relatório global de saúde de todas as instâncias WhatsApp ativas.
    Permite aos operadores detectar rapidamente lojistas desconectados ou sem bateria.
    """
    stmt = select(Company).where(Company.active.is_(True))
    res = await db.execute(stmt)
    companies = res.scalars().all()

    summaries = [connection_monitor.get_status_summary(c) for c in companies]
    offline_count = sum(
        1 for s in summaries if not s["is_connected"] and s["connection_status"] in connection_monitor.OFFLINE_STATES
    )

    return {
        "total_instances": len(summaries),
        "online_instances": len(summaries) - offline_count,
        "offline_instances": offline_count,
        "requires_attention": offline_count > 0,
        "instances": summaries,
    }
