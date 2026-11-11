import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger("whatsapp_engine.evolution_client")


async def send_whatsapp_message(
    instance_name: str,
    phone_number: str,
    text: str,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> bool:
    """
    Envia mensagem de texto ao WhatsApp do cliente via Evolution API v2 com
    política de retry e backoff exponencial (1s, 2s, 4s) para tolerância a falhas transitórias.
    """
    if not text or not phone_number or not instance_name:
        return False

    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/message/sendText/{instance_name}"
    headers = {"apikey": settings.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {
        "number": phone_number,
        "text": text,
    }

    last_error: Optional[str] = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    return True
                # Não retenta erros 4xx permanentes (exceto 429 Too Many Requests)
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    logger.error(
                        f"[EVOLUTION_SEND_ERROR] Erro cliente {resp.status_code} "
                        f"ao enviar para {phone_number} ({instance_name}): {resp.text}"
                    )
                    return False
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:
            last_error = str(exc)

        if attempt < max_retries:
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                f"[EVOLUTION_SEND_RETRY] Tentativa {attempt}/{max_retries} falhou "
                f"para {phone_number} ({instance_name}): {last_error}. Retentando em {delay:.1f}s..."
            )
            await asyncio.sleep(delay)

    logger.error(
        f"[EVOLUTION_SEND_FAILED] Falha definitiva após {max_retries} tentativas "
        f"para {phone_number} ({instance_name}): {last_error}"
    )
    return False


async def fetch_evolution_media_base64(
    instance_name: str,
    message_payload: Dict[str, Any],
    max_retries: int = 2,
) -> Optional[str]:
    """Obtém o conteúdo base64 da mídia através da Evolution API v2 com retry."""
    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/chat/getBase64FromMediaMessage/{instance_name}"
    headers = {"apikey": settings.EVOLUTION_API_KEY, "Content-Type": "application/json"}

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json={"message": message_payload})
                if resp.status_code in (200, 201):
                    data = resp.json()
                    return data.get("base64")
        except Exception as exc:
            logger.warning(
                f"[EVOLUTION_MEDIA_RETRY] Tentativa {attempt}/{max_retries} falhou ({instance_name}): {exc}"
            )
        if attempt < max_retries:
            await asyncio.sleep(1.0)
    return None
