import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.evolution_client import (
    calculate_human_typing_delay,
    mark_message_read,
    send_presence,
    simulate_human_typing_and_send,
)
from app.message_buffer import MessageDebouncer


def test_calculate_human_typing_delay_bounds():
    with patch.object(settings, "SIMULATE_HUMAN_BEHAVIOR", True):
        # Mensagem curta deve respeitar min_delay
        short_delay = calculate_human_typing_delay("oi", min_delay=1.0, max_delay=3.0)
        assert 1.0 <= short_delay <= 3.5

        # Mensagem longa deve respeitar teto max_delay
        long_text = "x" * 2000
        long_delay = calculate_human_typing_delay(long_text, min_delay=1.0, max_delay=4.0)
        assert long_delay <= 4.8  # teto + margem de jitter

        # Variações aleatórias (jitter)
        delays = {
            calculate_human_typing_delay("Cardápio de pizzas", min_delay=1.0, max_delay=3.0)
            for _ in range(5)
        }
        assert len(delays) > 1, "O cálculo de delay deve conter jitter aleatório"


@pytest.mark.asyncio
async def test_send_presence_and_mark_read():
    mock_post = AsyncMock()
    mock_post.return_value.status_code = 200

    with patch("httpx.AsyncClient.post", mock_post):
        # 1. Teste de presença 'composing'
        ok = await send_presence(
            instance_name="pizzaria_test",
            phone_number="5511999998888",
            presence="composing",
        )
        assert ok is True
        assert mock_post.call_count == 1
        call_url = mock_post.call_args[0][0]
        assert "/chat/sendPresence/pizzaria_test" in call_url

        # 2. Teste de markMessageAsRead
        read_ok = await mark_message_read(
            instance_name="pizzaria_test",
            remote_jid="5511999998888@s.whatsapp.net",
            message_id="MSG_12345",
        )
        assert read_ok is True
        assert mock_post.call_count == 2
        call_url = mock_post.call_args[0][0]
        assert "/chat/markMessageAsRead/pizzaria_test" in call_url


@pytest.mark.asyncio
async def test_simulate_human_typing_and_send():
    mock_read = AsyncMock(return_value=True)
    mock_presence = AsyncMock(return_value=True)
    mock_send = AsyncMock(return_value=True)

    with patch("app.evolution_client.mark_message_read", mock_read), \
         patch("app.evolution_client.send_presence", mock_presence), \
         patch("app.evolution_client.send_whatsapp_message", mock_send), \
         patch.object(settings, "SIMULATE_HUMAN_BEHAVIOR", True), \
         patch.object(settings, "HUMAN_MIN_TYPING_DELAY", 0.01), \
         patch.object(settings, "HUMAN_MAX_TYPING_DELAY", 0.05):

        result = await simulate_human_typing_and_send(
            instance_name="test_inst",
            phone_number="5511999998888",
            text="Olá, tudo bem? Segue o cardápio!",
            remote_jid="5511999998888@s.whatsapp.net",
            message_id="MSG_001",
            simulate_presence=True,
        )

        assert result is True
        assert mock_read.call_count == 1
        # Presença deve ter sido chamada com composing e depois com paused
        assert mock_presence.call_count == 2
        mock_send.assert_awaited_once_with(
            instance_name="test_inst",
            phone_number="5511999998888",
            text="Olá, tudo bem? Segue o cardápio!",
            max_retries=3,
        )


class FakeRedisForDebounce:
    """Mock assíncrono simples em memória para simular o comportamento de buffer do Redis."""
    def __init__(self):
        self.data = {}

    async def rpush(self, key, value):
        if key not in self.data:
            self.data[key] = []
        self.data[key].append(value)
        return len(self.data[key])

    async def lrange(self, key, start, end):
        items = self.data.get(key, [])
        if end == -1:
            return items[start:]
        return items[start:end + 1]

    async def incr(self, key):
        val = int(self.data.get(key, 0)) + 1
        self.data[key] = str(val)
        return val

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)

    async def expire(self, key, seconds):
        return True


@pytest.mark.asyncio
async def test_message_debouncer_single_message():
    redis = FakeRedisForDebounce()
    debouncer = MessageDebouncer(redis)

    with patch.object(settings, "DEBOUNCE_ENABLED", True), \
         patch.object(settings, "DEBOUNCE_WAIT_SECONDS", 0.05):

        should_process, text = await debouncer.buffer_inbound_message(
            company_id=1,
            phone_number="5511999997777",
            text="Oi, boa noite!",
        )

        assert should_process is True
        assert text == "Oi, boa noite!"


@pytest.mark.asyncio
async def test_message_debouncer_burst_consolidation():
    redis = FakeRedisForDebounce()
    debouncer = MessageDebouncer(redis)

    with patch.object(settings, "DEBOUNCE_ENABLED", True), \
         patch.object(settings, "DEBOUNCE_WAIT_SECONDS", 0.1):

        # Simula 2 mensagens enviadas quase no mesmo milissegundo pelo cliente
        task1 = asyncio.create_task(
            debouncer.buffer_inbound_message(
                company_id=1,
                phone_number="5511999997777",
                text="Oi",
            )
        )
        await asyncio.sleep(0.02)
        task2 = asyncio.create_task(
            debouncer.buffer_inbound_message(
                company_id=1,
                phone_number="5511999997777",
                text="Quero pedir uma pizza calabresa",
            )
        )

        res1, res2 = await asyncio.gather(task1, task2)

        # A primeira mensagem deve ter sido suprimida/bufferizada
        assert res1[0] is False
        assert not res1[1]

        # A segunda mensagem deve consolidar ambas em um único payload
        assert res2[0] is True
        assert "Oi" in res2[1]
        assert "Quero pedir uma pizza calabresa" in res2[1]
