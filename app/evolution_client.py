import asyncio
import logging
import random
from typing import Any, Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger("whatsapp_engine.evolution_client")


def calculate_human_typing_delay(
    text: str,
    min_delay: Optional[float] = None,
    max_delay: Optional[float] = None,
) -> float:
    """
    Calcula tempo de digitação humanizado proporcional ao volume do texto
    com variação aleatória (jitter) para quebrar padrões matemáticos mecânicos.
    """
    if not text or not settings.SIMULATE_HUMAN_BEHAVIOR:
        return 0.0

    min_d = min_delay if min_delay is not None else settings.HUMAN_MIN_TYPING_DELAY
    max_d = max_delay if max_delay is not None else settings.HUMAN_MAX_TYPING_DELAY

    # Média de digitação humana no celular/web (~25 a 35 caracteres por segundo)
    base = len(text) * 0.015
    jitter = random.uniform(0.3, 0.9)
    total = base + jitter
    return min(max_d, max(min_d, total))


async def send_presence(
    instance_name: str,
    phone_number: str,
    presence: str = "composing",
    delay_ms: int = 0,
) -> bool:
    """
    Dispara status de presença na Evolution API v2:
    - 'composing': Exibe 'Digitando...' no WhatsApp do cliente
    - 'paused': Remove o status de digitação
    - 'recording': Exibe 'Gravando áudio...'
    """
    if not instance_name or not phone_number:
        return False

    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/chat/sendPresence/{instance_name}"
    headers = {"apikey": settings.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {
        "number": phone_number,
        "presence": presence,
        "delay": delay_ms,
    }

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            return resp.status_code in (200, 201)
    except Exception as exc:
        logger.debug(f"[PRESENCE_WARN] Falha ao enviar presença '{presence}' para {phone_number}: {exc}")
        return False


async def mark_message_read(
    instance_name: str,
    remote_jid: str,
    message_id: str,
) -> bool:
    """
    Marca a mensagem recebida como lida na Evolution API v2 com confirmação de leitura (blue ticks).
    """
    if not instance_name or not remote_jid or not message_id:
        return False

    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/chat/markMessageAsRead/{instance_name}"
    headers = {"apikey": settings.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {
        "read_messages": [
            {
                "remoteJid": remote_jid,
                "id": message_id,
                "fromMe": False,
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            return resp.status_code in (200, 201)
    except Exception as exc:
        logger.debug(f"[READ_RECEIPT_WARN] Falha ao marcar mensagem lida ({message_id}): {exc}")
        return False


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


async def simulate_human_typing_and_send(
    instance_name: str,
    phone_number: str,
    text: str,
    remote_jid: Optional[str] = None,
    message_id: Optional[str] = None,
    simulate_presence: bool = True,
    max_retries: int = 3,
) -> bool:
    """
    Executa a cadência de simulação anti-banimento humana:
    1. Marcação como lida com atraso orgânico (1s a 2s)
    2. Emissão de status 'Digitando...' (composing)
    3. Atraso proporcional ao tamanho da resposta com jitter
    4. Envio do texto com retry/backoff
    5. Remoção do status de digitação (paused)
    """
    if not text or not phone_number or not instance_name:
        return False

    should_simulate = simulate_presence and settings.SIMULATE_HUMAN_BEHAVIOR

    if should_simulate:
        # Atraso sutil para marcar mensagem recebida como lida
        if remote_jid and message_id:
            await asyncio.sleep(random.uniform(0.3, 0.7))
            asyncio.create_task(mark_message_read(instance_name, remote_jid, message_id))

        # Ativa o "Digitando..." no celular do cliente
        await send_presence(instance_name, phone_number, presence="composing")

        # Tempo em que o atendente estaria digitando o texto
        typing_delay = calculate_human_typing_delay(text)
        if typing_delay > 0:
            await asyncio.sleep(typing_delay)

    # Envia a mensagem real
    sent = await send_whatsapp_message(
        instance_name=instance_name,
        phone_number=phone_number,
        text=text,
        max_retries=max_retries,
    )

    if should_simulate:
        # Pausa o status de digitação
        await send_presence(instance_name, phone_number, presence="paused")

    return sent


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
