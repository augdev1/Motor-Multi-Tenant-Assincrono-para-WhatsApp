import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import (
    DistributedLock,
    EventDeduplicator,
    LockContentionError,
)
from app.evolution_client import fetch_evolution_media_base64, send_whatsapp_message
from app.graph import build_bot_graph
from app.logging_config import log_audit_event
from app.models import AuditLog, Company, OrderRecord
from app.schemas import BotState, EvolutionMessagePayload
from app.vision import extract_receipt_info

# Grafo padrão reutilizado entre execuções
_default_bot_graph = build_bot_graph()


async def _dispatch_message(
    instance_name: str,
    phone_number: str,
    text: str,
    await_send: bool = False,
) -> bool:
    """Envia mensagem ao WhatsApp de forma bloqueante (worker) ou em background (HTTP inline)."""
    if await_send:
        return await send_whatsapp_message(instance_name, phone_number, text)
    asyncio.create_task(send_whatsapp_message(instance_name, phone_number, text))
    return True


async def process_webhook_payload(
    payload: EvolutionMessagePayload,
    db: AsyncSession,
    redis=None,
    bot_graph=None,
    skip_dedup: bool = False,
    await_send: bool = False,
    extract_receipt_fn=extract_receipt_info,
) -> Tuple[int, Dict[str, Any]]:
    """
    Executa o pipeline completo de processamento de mensagem entrante:
    deduplicação -> resolução de tenant -> distributed lock -> Vision/LangGraph -> auditoria -> envio.
    Retorna (http_status_code, response_dict).
    """
    start_time = time.time()
    graph = bot_graph or _default_bot_graph

    instance_name = payload.get_instance_name()
    phone_number = payload.get_phone_number()
    message_id = payload.get_message_id()
    text = payload.get_text_content()
    customer_name = payload.get_customer_name()

    if not instance_name or not phone_number:
        return status.HTTP_200_OK, {"status": "ignored", "reason": "missing_instance_or_phone"}

    # 1. Deduplicação de Eventos (SET NX EX) quando não verificada na borda
    if not skip_dedup and redis and message_id:
        deduplicator = EventDeduplicator(redis)
        is_new = await deduplicator.check_and_set(message_id, ttl_seconds=300)
        if not is_new:
            log_audit_event(
                event_type="EVENT_DEDUPLICATED",
                phone_number=phone_number,
                details={"message_id": message_id, "action": "ignored"},
            )
            return status.HTTP_200_OK, {"status": "ignored", "reason": "duplicate_event"}

    # 2. Resolução estrita do tenant
    stmt = select(Company).where(Company.instance_name == instance_name, Company.active.is_(True))
    result = await db.execute(stmt)
    company = result.scalar_one_or_none()

    if not company:
        return status.HTTP_200_OK, {
            "status": "ignored",
            "reason": "company_not_found",
            "message": f"Instância '{instance_name}' não mapeada ou inativa.",
        }

    company_id = company.id

    # 3. Distributed Lock por cliente e tenant
    lock = None
    if redis:
        lock = DistributedLock(
            redis_client=redis,
            company_id=company_id,
            phone_number=phone_number,
            lease_timeout=10,
        )
        try:
            await lock.__aenter__()
            log_audit_event(
                event_type="DISTRIBUTED_LOCK_ACQUIRED",
                company_id=company_id,
                phone_number=phone_number,
                details={"lease_timeout": 10},
            )
        except LockContentionError:
            log_audit_event(
                event_type="DISTRIBUTED_LOCK_CONTENTION",
                company_id=company_id,
                phone_number=phone_number,
                details={"reason": "lock_busy"},
                level=logging.WARNING,
            )
            # Desfaz marcação de deduplicação para permitir retentativa
            if redis and message_id:
                await redis.delete(f"dedup:msg:{message_id}")
            return status.HTTP_429_TOO_MANY_REQUESTS, {
                "status": "busy",
                "reason": "lock_contention",
            }

    try:
        # 4. Tratamento de Comprovante de Pagamento (Gemini Vision)
        if payload.is_image_or_document():
            media_b64 = payload.get_inline_base64()
            if not media_b64:
                media_b64 = await fetch_evolution_media_base64(
                    instance_name, (payload.data or {}).get("message", {})
                )

            if media_b64:
                receipt = await extract_receipt_fn(
                    media_b64,
                    api_key=(company.settings or {}).get("gemini_api_key"),
                )
                if receipt and receipt.get("is_receipt"):
                    pagador = receipt.get("pagador") or "Não identificado"
                    valor = receipt.get("valor") or 0.0
                    trans_id = receipt.get("id_transacao") or "N/D"

                    order_stmt = (
                        select(OrderRecord)
                        .where(
                            OrderRecord.company_id == company_id,
                            OrderRecord.customer_phone == phone_number,
                        )
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
                        await _dispatch_message(instance_name, cozinha_phone, comanda_cozinha, await_send=await_send)

                    await _dispatch_message(instance_name, phone_number, bot_response, await_send=await_send)

                    return status.HTTP_200_OK, {
                        "status": "processed",
                        "receipt_verified": True,
                        "pagador_pix": pagador,
                        "valor": valor,
                        "response": bot_response,
                    }
                else:
                    bot_response = (
                        "Recebemos a sua imagem, mas não conseguimos identificar um comprovante de pagamento válido nela. "
                        "Por favor, envie uma foto ou print nítido do comprovante Pix onde apareça o valor e a data legíveis!"
                    )
                    await _dispatch_message(instance_name, phone_number, bot_response, await_send=await_send)
                    return status.HTTP_200_OK, {
                        "status": "processed",
                        "receipt_verified": False,
                        "response": bot_response,
                    }

        # 5. Execução da Máquina de Estados (LangGraph) para Mensagens de Texto
        initial_state = BotState(
            instance_name=instance_name,
            phone_number=phone_number,
            customer_name=customer_name,
            user_input=text,
        )

        final_state = await graph.ainvoke(
            initial_state,
            config={"configurable": {"db": db}},
        )

        bot_response = final_state.get("bot_response", "")
        if company.segment in ["pizzaria_lanches", "pizzaria", "lanchonete"]:
            bot_response = bot_response.replace("*", "")

        if bot_response:
            sent = await _dispatch_message(instance_name, phone_number, bot_response, await_send=await_send)
            if await_send and not sent:
                dlq_audit = AuditLog(
                    company_id=company_id,
                    event_type="WHATSAPP_SEND_FAILED",
                    phone_number=phone_number,
                    payload={"message_id": message_id, "response": bot_response},
                    execution_time_ms=0,
                )
                db.add(dlq_audit)

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
            },
        )

        return status.HTTP_200_OK, {
            "status": "processed",
            "response": bot_response,
            "human_handoff": final_state.get("human_handoff", False),
            "order_id": final_state.get("order_id"),
            "current_step": final_state.get("current_step"),
        }

    finally:
        if lock:
            await lock.__aexit__(None, None, None)
            log_audit_event(
                event_type="DISTRIBUTED_LOCK_RELEASED",
                company_id=company_id,
                phone_number=phone_number,
            )
