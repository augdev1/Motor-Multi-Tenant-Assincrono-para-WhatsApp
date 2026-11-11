import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, Optional

import httpx
import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.connection_monitor import connection_monitor
from app.database import (
    DistributedLock,
    EventDeduplicator,
    LockContentionError,
    get_db,
)
from app.graph import build_bot_graph
from app.logging_config import log_audit_event
from app.models import AuditLog, Company, CustomerSession, OrderRecord
from app.schemas import BotState, EvolutionMessagePayload
from app.vision import extract_receipt_info

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
    version="0.1.0",
    lifespan=lifespan,
)


async def send_whatsapp_message(instance_name: str, phone_number: str, text: str) -> bool:
    """Envia a mensagem de resposta de volta ao WhatsApp do cliente através da Evolution API v2."""
    if not text or not phone_number or not instance_name:
        return False
    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/message/sendText/{instance_name}"
    headers = {"apikey": settings.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {
        "number": phone_number,
        "text": text,
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            return resp.status_code in (200, 201)
    except Exception:
        return False


async def fetch_evolution_media_base64(instance_name: str, message_payload: Dict[str, Any]) -> Optional[str]:
    """Obtém o conteúdo base64 da mídia através da Evolution API v2."""
    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/chat/getBase64FromMediaMessage/{instance_name}"
    headers = {"apikey": settings.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json={"message": message_payload})
            if resp.status_code in (200, 201):
                data = resp.json()
                return data.get("base64")
    except Exception:
        pass
    return None


def get_redis_client():
    """Dependency injection para o cliente Redis."""
    global redis_client
    return redis_client


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "whatsapp-multi-tenant-engine"}


@app.post("/webhook/evolution")
async def evolution_webhook(
    payload: EvolutionMessagePayload,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis_client),
):
    """
    Endpoint principal para recebimento de webhooks da Evolution API v2.
    Aplica deduplicação de eventos, locks distribuídos e máquina de estados LangGraph.
    """
    start_time = time.time()

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
    text = payload.get_text_content()
    customer_name = payload.get_customer_name()

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

    # 2. Deduplicação de Eventos (SET NX EX)
    if redis and message_id:
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

    # 3. Resolução prévia do tenant para chave de lock estrita
    stmt = select(Company).where(Company.instance_name == instance_name, Company.active.is_(True))
    result = await db.execute(stmt)
    company = result.scalar_one_or_none()

    if not company:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ignored",
                "reason": "company_not_found",
                "message": f"Instância '{instance_name}' não mapeada ou inativa."
            }
        )

    company_id = company.id

    # 4. Distributed Lock por cliente e tenant
    lock = None
    if redis:
        lock = DistributedLock(
            redis_client=redis,
            company_id=company_id,
            phone_number=phone_number,
            lease_timeout=10
        )
        try:
            await lock.__aenter__()
            log_audit_event(
                event_type="DISTRIBUTED_LOCK_ACQUIRED",
                company_id=company_id,
                phone_number=phone_number,
                details={"lease_timeout": 10}
            )
        except LockContentionError:
            log_audit_event(
                event_type="DISTRIBUTED_LOCK_CONTENTION",
                company_id=company_id,
                phone_number=phone_number,
                details={"reason": "lock_busy"},
                level=logging.WARNING
            )
            # Em colisão de concorrência, desfaz a marcação de deduplicação para permitir retentativa
            if redis and message_id:
                await redis.delete(f"dedup:msg:{message_id}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"status": "busy", "reason": "lock_contention"}
            )

    try:
        # 5. Tratamento Especial de Comprovante de Pagamento (Gemini Vision)
        if payload.is_image_or_document():
            media_b64 = payload.get_inline_base64()
            if not media_b64:
                media_b64 = await fetch_evolution_media_base64(instance_name, (payload.data or {}).get("message", {}))

            if media_b64:
                receipt = await extract_receipt_info(
                    media_b64,
                    api_key=(company.settings or {}).get("gemini_api_key")
                )
                if receipt and receipt.get("is_receipt"):
                    pagador = receipt.get("pagador") or "Não identificado"
                    valor = receipt.get("valor") or 0.0
                    trans_id = receipt.get("id_transacao") or "N/D"

                    # Localiza o último pedido do cliente para confirmar pagamento
                    order_stmt = (
                        select(OrderRecord)
                        .where(OrderRecord.company_id == company_id, OrderRecord.customer_phone == phone_number)
                        .order_by(OrderRecord.created_at.desc())
                        .limit(1)
                    )
                    order_res = await db.execute(order_stmt)
                    last_order = order_res.scalar_one_or_none()

                    if last_order:
                        last_order.status = "confirmado_pago"
                        last_order.payment_method = f"PIX (Titular: {pagador})"
                        await db.commit()

                    bot_response = (
                        f"Comprovante Pix recebido e validado com sucesso! 🎉\n\n"
                        f"• Titular da Conta Bancária: {pagador}\n"
                        f"• Valor Confirmado: R$ {valor:.2f}\n\n"
                        "Seu pedido foi confirmado e já está sendo preparado pela nossa equipe! "
                        "Em 35 a 50 minutos estará chegando até você. 🍕🚀"
                    )

                    # Despacho da Comanda para o WhatsApp da Cozinha/Produção
                    cozinha_phone = (company.settings or {}).get("whatsapp_cozinha")
                    if cozinha_phone:
                        itens_desc = "Itens do Pedido"
                        entrega_desc = "Não informado"
                        if last_order:
                            itens_desc = "\n".join(
                                f"  • {it.get('descricao', 'Item')} (x{it.get('quantidade', 1)})"
                                for it in (last_order.items or [])
                            )
                            entrega_desc = last_order.delivery_address or "Balcão"

                        comanda_cozinha = (
                            "🔔 NOVO PEDIDO CONFIRMADO (PIX VALIDADO) 🔔\n"
                            "────────────────────────────────────────\n"
                            f"👤 Cliente no WhatsApp: {customer_name} ({phone_number})\n"
                            f"🏦 NOME NO EXTRATO BANCÁRIO (PAGADOR): {pagador}\n"
                            f"💰 Valor do Pix: R$ {valor:.2f}\n"
                            f"🧾 Autenticação: {trans_id}\n"
                            f"📍 Endereço de Entrega: {entrega_desc}\n\n"
                            f"📦 ITENS PARA PRODUÇÃO:\n{itens_desc}\n"
                            "────────────────────────────────────────"
                        )
                        asyncio.create_task(send_whatsapp_message(instance_name, cozinha_phone, comanda_cozinha))

                    asyncio.create_task(send_whatsapp_message(instance_name, phone_number, bot_response))

                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={
                            "status": "processed",
                            "receipt_verified": True,
                            "pagador_pix": pagador,
                            "valor": valor,
                            "response": bot_response
                        }
                    )
                else:
                    bot_response = (
                        "Recebemos a sua imagem, mas não conseguimos identificar um comprovante de pagamento válido nela. "
                        "Por favor, envie uma foto ou print nítido do comprovante Pix onde apareça o valor e a data legíveis!"
                    )
                    asyncio.create_task(send_whatsapp_message(instance_name, phone_number, bot_response))
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={"status": "processed", "receipt_verified": False, "response": bot_response}
                    )

        # 6. Execução da Máquina de Estados (LangGraph) para Mensagens de Texto
        initial_state = BotState(
            instance_name=instance_name,
            phone_number=phone_number,
            customer_name=customer_name,
            user_input=text,
        )

        final_state = await bot_graph.ainvoke(
            initial_state,
            config={"configurable": {"db": db}}
        )

        # 6. Registro de Auditoria e Observabilidade
        bot_response = final_state.get("bot_response", "")
        # Se for o segmento de pizzaria/lanches, aplica regra sem asteriscos
        if company.segment in ["pizzaria_lanches", "pizzaria", "lanchonete"]:
            bot_response = bot_response.replace("*", "")

        # 7. Envio ativo da mensagem para o WhatsApp via Evolution API v2 (não-bloqueante)
        if bot_response:
            asyncio.create_task(send_whatsapp_message(instance_name, phone_number, bot_response))

        elapsed_ms = int((time.time() - start_time) * 1000)
        audit = AuditLog(
            company_id=company_id,
            event_type="message_processed",
            phone_number=phone_number,
            payload={
                "message_id": message_id,
                "input": text,
                "response": bot_response,
                "step": final_state.get("current_step"),
                "human_handoff": final_state.get("human_handoff"),
            },
            execution_time_ms=elapsed_ms,
        )
        db.add(audit)
        await db.commit()

        log_audit_event(
            event_type="MESSAGE_PROCESSED_SUCCESS",
            company_id=company_id,
            phone_number=phone_number,
            execution_time_ms=elapsed_ms,
            details={
                "message_id": message_id,
                "step": final_state.get("current_step"),
                "human_handoff": final_state.get("human_handoff"),
                "has_order": bool(final_state.get("order_id")),
            }
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "processed",
                "response": bot_response,
                "human_handoff": final_state.get("human_handoff", False),
                "order_id": final_state.get("order_id"),
                "current_step": final_state.get("current_step"),
            }
        )

    finally:
        # Liberação segura do lock distribuído
        if lock:
            await lock.__aexit__(None, None, None)
            log_audit_event(
                event_type="DISTRIBUTED_LOCK_RELEASED",
                company_id=company_id,
                phone_number=phone_number
            )


@app.get("/audit/logs/{company_id_or_instance}")
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


@app.get("/api/companies/{company_id_or_instance}/connection-status")
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


@app.get("/api/instances/health")
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

