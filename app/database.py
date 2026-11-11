import hashlib
import math
import re
import uuid
from typing import AsyncGenerator, List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import settings
from app.models import KnowledgeChunk

# Criação da engine e factory de sessões assíncronas
engine = create_async_engine(
    settings.async_postgres_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency injection para sessões assíncronas do banco."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


class LockContentionError(Exception):
    """Exceção levantada quando um lock distribuído não pode ser adquirido."""
    pass


class EventDeduplicator:
    """Garante idempotência de eventos processados via Redis SET NX EX."""

    def __init__(self, redis_client):
        self.redis = redis_client

    async def check_and_set(self, message_id: str, ttl_seconds: int = 300) -> bool:
        """
        Registra a mensagem no Redis usando SET NX EX.
        Retorna True se for a primeira vez que a mensagem é vista.
        Retorna False se já existir (evento duplicado).
        """
        if not message_id:
            return True
        key = f"dedup:msg:{message_id}"
        # SET key value NX EX ttl
        acquired = await self.redis.set(key, "1", nx=True, ex=ttl_seconds)
        return bool(acquired)


class DistributedLock:
    """Lock distribuído atômico por cliente e tenant via Redis."""

    def __init__(self, redis_client, company_id: str, phone_number: str, lease_timeout: int = 10):
        self.redis = redis_client
        self.company_id = company_id
        self.phone_number = phone_number
        self.lease_timeout = lease_timeout
        self.lock_key = f"lock:company:{company_id}:user:{phone_number}"
        self.token = str(uuid.uuid4())
        self._acquired = False

    async def __aenter__(self):
        # Tenta adquirir a trava atômica
        result = await self.redis.set(
            self.lock_key,
            self.token,
            nx=True,
            ex=self.lease_timeout
        )
        if not result:
            raise LockContentionError(f"Conflito de lock para cliente {self.phone_number} na empresa {self.company_id}")
        self._acquired = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._acquired:
            try:
                # Liberação segura: remove apenas se o token for o mesmo
                current_val = await self.redis.get(self.lock_key)
                val_str = current_val.decode("utf-8") if isinstance(current_val, bytes) else current_val
                if val_str == self.token:
                    await self.redis.delete(self.lock_key)
            except Exception:
                pass


def calculate_cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calcula a similaridade de cosseno entre dois vetores."""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm_a = math.sqrt(sum(a * a for a, b in zip(vec1, vec2)))
    norm_b = math.sqrt(sum(b * b for a, b in zip(vec1, vec2)))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)


def generate_mock_embedding(text: str, dim: int = 768) -> List[float]:
    """
    Gera um embedding determinístico de 768 dimensões baseado em termos e hash do texto.
    100% offline, rápido e sem custo de tokens, com extração de palavras-chave e normalização.
    """
    vector = [0.0] * dim
    words = re.findall(r"\w+", text.lower())
    stopwords = {"qual", "o", "a", "os", "as", "de", "do", "da", "dos", "das", "em", "no", "na", "para", "com", "por", "um", "uma", "vocês", "tem", "têm"}
    keywords = [w for w in words if w not in stopwords]
    if not keywords:
        keywords = words or ["padrao"]

    # Hash dos termos para projetar em índices determinísticos
    for word in keywords:
        h = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16)
        index = h % dim
        vector[index] += 1.0

    # Normalização L2
    norm = math.sqrt(sum(x * x for x in vector))
    if norm > 0:
        vector = [x / norm for x in vector]
    else:
        vector[0] = 1.0
    return vector


async def search_knowledge_chunks(
    db: AsyncSession,
    company_id: str,
    query_embedding: List[float],
    top_k: int = 3,
    min_similarity: float = 0.1
) -> List[Tuple[KnowledgeChunk, float]]:
    """
    Busca semântica no catálogo estritamente filtrada por company_id (isolamento absoluto).
    Em PostgreSQL utiliza operador nativo pgvector (<=>) com índice HNSW no banco;
    em SQLite (testes offline) executa cálculo de cosseno em memória.
    """
    try:
        dialect_name = db.get_bind().dialect.name
    except Exception:
        dialect_name = ""

    if dialect_name == "postgresql":
        distance_expr = KnowledgeChunk.embedding.cosine_distance(query_embedding)
        similarity_expr = (1.0 - distance_expr).label("similarity")
        stmt = (
            select(KnowledgeChunk, similarity_expr)
            .where(KnowledgeChunk.company_id == company_id)
            .order_by(distance_expr.asc())
            .limit(top_k)
        )
        result = await db.execute(stmt)
        return [
            (chunk, float(sim))
            for chunk, sim in result.all()
            if float(sim) >= min_similarity
        ]

    stmt = select(KnowledgeChunk).where(KnowledgeChunk.company_id == company_id)
    result = await db.execute(stmt)
    chunks = result.scalars().all()

    scored_chunks: List[Tuple[KnowledgeChunk, float]] = []
    for chunk in chunks:
        if chunk.embedding:
            sim = calculate_cosine_similarity(query_embedding, chunk.embedding)
            if sim >= min_similarity:
                scored_chunks.append((chunk, sim))

    # Ordenar por maior similaridade
    scored_chunks.sort(key=lambda x: x[1], reverse=True)
    return scored_chunks[:top_k]
