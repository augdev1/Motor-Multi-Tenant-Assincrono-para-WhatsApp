import hashlib
import json
import logging
from typing import List, Optional

from google import genai
from google.genai import types

from app.config import settings
from app.database import generate_mock_embedding

logger = logging.getLogger("whatsapp_engine.embeddings")

MOCK_KEYS = {"", "mock-key", "mock-gemini-key", "sua_chave_real_do_gemini_aqui"}


def _has_real_gemini_key(api_key: Optional[str] = None) -> bool:
    key = (api_key or settings.GEMINI_API_KEY or "").strip()
    return bool(key) and key not in MOCK_KEYS and not key.startswith("mock")


async def generate_semantic_embedding(
    text: str,
    api_key: Optional[str] = None,
    redis_client=None,
) -> List[float]:
    """
    Gera vetor denso de 768 dimensões utilizando Gemini (models/gemini-embedding-001).
    Utiliza cache em Redis (TTL 24h) para consultas repetidas e fallback determinístico
    automático em ambientes offline/mock ou em caso de indisponibilidade transitória.
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return generate_mock_embedding("")

    effective_key = (api_key or settings.GEMINI_API_KEY or "").strip()
    if not settings.USE_REAL_EMBEDDINGS or not _has_real_gemini_key(effective_key):
        return generate_mock_embedding(clean_text)

    cache_key = f"emb:v1:{hashlib.sha256(clean_text.lower().encode('utf-8')).hexdigest()}"
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return [float(x) for x in json.loads(cached)]
        except Exception:
            pass

    try:
        client = genai.Client(api_key=effective_key)
        res = await client.aio.models.embed_content(
            model="models/gemini-embedding-001",
            contents=clean_text,
            config=types.EmbedContentConfig(output_dimensionality=768),
        )
        vector = list(res.embeddings[0].values) if res.embeddings else []
        if len(vector) == 768:
            if redis_client:
                try:
                    await redis_client.set(cache_key, json.dumps(vector), ex=86400)
                except Exception:
                    pass
            return [float(x) for x in vector]
    except Exception as exc:
        logger.warning(
            f"Falha ao gerar embedding semântico via Gemini ({exc}); aplicando fallback determinístico."
        )

    return generate_mock_embedding(clean_text)
