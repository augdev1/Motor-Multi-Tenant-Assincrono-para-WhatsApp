import asyncio
import json
import logging
import signal
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

from app.config import settings
from app.database import AsyncSessionLocal
from app.schemas import EvolutionMessagePayload
from app.webhook_processor import process_webhook_payload

logger = logging.getLogger("whatsapp_engine.queue_worker")

QUEUE_PENDING = "queue:webhooks:pending"
QUEUE_PROCESSING = "queue:webhooks:processing"
QUEUE_DLQ = "queue:webhooks:dlq"
MAX_JOB_ATTEMPTS = 3


async def enqueue_webhook_event(redis_client, payload_dict: Dict[str, Any]) -> bool:
    """
    Publica um evento de webhook validado na fila confiável do Redis para
    processamento assíncrono pelos workers dedicados.
    """
    envelope = json.dumps({
        "payload": payload_dict,
        "attempts": 0,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    })
    await redis_client.lpush(QUEUE_PENDING, envelope)
    return True


async def process_next_job(
    redis_client,
    session_factory=AsyncSessionLocal,
    blocking: bool = False,
    timeout: int = 2,
    bot_graph=None,
) -> Optional[Dict[str, Any]]:
    """
    Consome atomicamente o próximo job da fila (pattern reliable queue via LMOVE/BLMOVE).
    Executa ACK removendo de QUEUE_PROCESSING após conclusão, re-enfileira em colisão de lock/falha
    ou encaminha para QUEUE_DLQ após esgotar MAX_JOB_ATTEMPTS.
    """
    if blocking:
        raw_job = await redis_client.blmove(
            QUEUE_PENDING, QUEUE_PROCESSING, timeout, "RIGHT", "LEFT"
        )
    else:
        raw_job = await redis_client.lmove(
            QUEUE_PENDING, QUEUE_PROCESSING, "RIGHT", "LEFT"
        )

    if not raw_job:
        return None

    raw_str = raw_job.decode("utf-8") if isinstance(raw_job, bytes) else raw_job
    try:
        envelope = json.loads(raw_str)
    except Exception as exc:
        logger.error(f"[WORKER_MALFORMED_JOB] Removendo job inválido para DLQ: {exc}")
        await redis_client.lrem(QUEUE_PROCESSING, 1, raw_str)
        await redis_client.lpush(QUEUE_DLQ, json.dumps({"raw": raw_str, "error": str(exc)}))
        return {"status": "dlq", "reason": "malformed_json"}

    attempts = int(envelope.get("attempts", 0))
    payload_dict = envelope.get("payload", {})

    try:
        payload = EvolutionMessagePayload(**payload_dict)
        async with session_factory() as db:
            try:
                status_code, content = await process_webhook_payload(
                    payload=payload,
                    db=db,
                    redis=redis_client,
                    bot_graph=bot_graph,
                    skip_dedup=True,
                    await_send=True,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        if status_code == 429:
            # Lock busy: re-enfileira se ainda houver tentativas disponíveis
            return await _retry_or_dlq(
                redis_client, raw_str, envelope, attempts, reason="lock_contention"
            )

        # ACK: processamento concluído com sucesso
        await redis_client.lrem(QUEUE_PROCESSING, 1, raw_str)
        return {"status": "ack", "http_status": status_code, "content": content}

    except Exception as exc:
        logger.exception(f"[WORKER_JOB_ERROR] Erro ao processar job (tentativa {attempts + 1}): {exc}")
        return await _retry_or_dlq(
            redis_client, raw_str, envelope, attempts, reason=str(exc)
        )


async def _retry_or_dlq(
    redis_client,
    raw_str: str,
    envelope: Dict[str, Any],
    attempts: int,
    reason: str,
) -> Dict[str, Any]:
    """Re-enfileira o job até MAX_JOB_ATTEMPTS ou envia definitivamente para a DLQ."""
    await redis_client.lrem(QUEUE_PROCESSING, 1, raw_str)
    next_attempts = attempts + 1

    if next_attempts < MAX_JOB_ATTEMPTS:
        envelope["attempts"] = next_attempts
        envelope["last_error"] = reason
        await redis_client.lpush(QUEUE_PENDING, json.dumps(envelope))
        logger.warning(
            f"[WORKER_REQUEUE] Job re-enfileirado (tentativa {next_attempts}/{MAX_JOB_ATTEMPTS}): {reason}"
        )
        return {"status": "requeued", "attempts": next_attempts, "reason": reason}

    envelope["attempts"] = next_attempts
    envelope["failed_at"] = datetime.now(timezone.utc).isoformat()
    envelope["last_error"] = reason
    await redis_client.lpush(QUEUE_DLQ, json.dumps(envelope))
    logger.error(f"[WORKER_DLQ] Job movido para Dead-Letter Queue após {next_attempts} falhas: {reason}")
    return {"status": "dlq", "attempts": next_attempts, "reason": reason}


async def run_worker() -> None:
    """Loop principal do worker assíncrono com shutdown gracioso."""
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Sinal de encerramento recebido. Finalizando worker graciosamente...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    logger.info("🚀 Webhook Queue Worker iniciado. Aguardando eventos em '%s'...", QUEUE_PENDING)

    try:
        while not stop_event.is_set():
            try:
                await process_next_job(redis_client, blocking=True, timeout=2)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[WORKER_LOOP_ERROR] Erro transitório no loop do worker: {exc}")
                await asyncio.sleep(2.0)
    finally:
        await redis_client.aclose()
        logger.info("Worker encerrado.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
