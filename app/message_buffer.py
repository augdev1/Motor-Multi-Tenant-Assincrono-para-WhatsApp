import asyncio
import logging
from typing import Optional, Tuple

from app.config import settings

logger = logging.getLogger("whatsapp_engine.message_buffer")


class MessageDebouncer:
    """
    Agrupa mensagens picadas recebidas em intervalo curto (debounce window) no Redis,
    evitando múltiplas respostas em rajada, economizando chamadas de IA e simulando
    o tempo natural de leitura de um atendente humano.
    """

    def __init__(self, redis_client):
        self.redis = redis_client

    async def buffer_inbound_message(
        self,
        company_id: str,
        phone_number: str,
        text: str,
        wait_seconds: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Registra a mensagem em buffer no Redis e aguarda a janela de debounce.
        Retorna:
          - (True, combined_text): Se esta execução venceu a janela e deve processar todas as mensagens acumuladas.
          - (False, ""): Se uma mensagem mais recente chegou durante a janela e assumiu a consolidação.
        """
        clean_text = (text or "").strip()
        delay = wait_seconds if wait_seconds is not None else settings.DEBOUNCE_WAIT_SECONDS

        if not self.redis or not clean_text or not settings.DEBOUNCE_ENABLED or delay <= 0:
            return True, clean_text

        key_msgs = f"debounce:msgs:{company_id}:{phone_number}"
        key_ver = f"debounce:ver:{company_id}:{phone_number}"

        try:
            # 1. Enfileira a mensagem atual no buffer da conversa
            await self.redis.rpush(key_msgs, clean_text)
            await self.redis.expire(key_msgs, 60)

            # 2. Incrementa o versionador atômico
            version = await self.redis.incr(key_ver)
            await self.redis.expire(key_ver, 60)

            # 3. Aguarda a janela de agrupamento (ex: 3.0 segundos)
            await asyncio.sleep(delay)

            # 4. Verifica se uma nova mensagem chegou durante a espera
            current_ver_raw = await self.redis.get(key_ver)
            current_ver = int(current_ver_raw) if current_ver_raw is not None else version

            if current_ver != version:
                logger.info(
                    f"[DEBOUNCE] Mensagem de {phone_number} consolidada em versão posterior "
                    f"(ver={version} < current={current_ver})."
                )
                return False, ""

            # 5. Execução vencedora: extrai todo o histórico acumulado
            raw_items = await self.redis.lrange(key_msgs, 0, -1)
            await self.redis.delete(key_msgs, key_ver)

            if not raw_items:
                return True, clean_text

            decoded_messages = []
            for item in raw_items:
                if isinstance(item, bytes):
                    decoded_messages.append(item.decode("utf-8"))
                else:
                    decoded_messages.append(str(item))

            combined_text = "\n".join(decoded_messages)
            if len(decoded_messages) > 1:
                logger.info(
                    f"[DEBOUNCE_CONSOLIDATED] {len(decoded_messages)} mensagens agrupadas para {phone_number} "
                    f"em 1 única requisição: {combined_text!r}"
                )
            return True, combined_text

        except Exception as exc:
            logger.warning(f"[DEBOUNCE_WARN] Erro ao aplicar debounce ({exc}). Prosseguindo com texto original.")
            return True, clean_text
