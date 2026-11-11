#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE INICIALIZAÇÃO, MIGRAÇÃO E PROVISIONAMENTO DE PRODUÇÃO
# ==============================================================================
# Uso: bash scripts/start_prod.sh
# ==============================================================================

set -euo pipefail

COMPOSE_FILE="docker-compose.prod.yml"

echo "======================================================================"
echo ">>> INICIANDO DEPLOY DA STACK EM PRODUÇÃO"
echo "======================================================================"

# 1. Validação de existência do arquivo .env
if [[ ! -f ".env" ]]; then
    echo "❌ Erro: Arquivo .env não localizado no diretório atual ($(pwd))." >&2
    echo "Crie o arquivo .env a partir do template .env.prod.example:" >&2
    echo "  cp .env.prod.example .env && nano .env" >&2
    exit 1
fi

echo "✅ Arquivo .env detectado com sucesso."

# 2. Subida e build dos containers em segundo plano
echo ">>> Subindo os containers com Docker Compose (${COMPOSE_FILE})..."
docker compose -f "${COMPOSE_FILE}" up -d --build

# 3. Aguardo do healthcheck do PostgreSQL com timeout de 60s
echo ">>> Aguardando o PostgreSQL inicializar e ficar saudável..."
MAX_ATTEMPTS=30
ATTEMPT=1
until [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; do
    STATUS=$(docker compose -f "${COMPOSE_FILE}" ps postgres --format "{{.Health}}" 2>/dev/null || echo "")
    if [[ "$STATUS" == *"healthy"* ]]; then
        echo "✅ PostgreSQL está saudável e pronto para conexões!"
        break
    fi
    echo "   Tentativa ${ATTEMPT}/${MAX_ATTEMPTS} - PostgreSQL ainda inicializando... (Aguardando 2s)"
    sleep 2
    ATTEMPT=$((ATTEMPT + 1))
done

if [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
    echo "❌ Timeout aguardando healthcheck do PostgreSQL. Verifique os logs:" >&2
    docker compose -f "${COMPOSE_FILE}" logs postgres
    exit 1
fi

# 4. Execução de migrações versionadas (Alembic + pgvector HNSW)
echo ">>> [1/2] Executando migrações de banco de dados (alembic upgrade head)..."
docker compose -f "${COMPOSE_FILE}" exec -T api alembic upgrade head

# 5. Execução do seed do primeiro tenant (Sabor & Forno Pizzaria e Lanches)
echo ">>> [2/2] Executando semente do primeiro tenant e catálogo RAG (app.seed_lanchonete)..."
docker compose -f "${COMPOSE_FILE}" exec -T api python -m app.seed_lanchonete

# 6. Exibição do status dos containers
echo "======================================================================"
echo ">>> STATUS FINAL DOS CONTAINERS EM PRODUÇÃO:"
echo "======================================================================"
docker compose -f "${COMPOSE_FILE}" ps

echo ""
echo "======================================================================"
echo ">>> DEPLOY DE PRODUÇÃO CONCLUÍDO COM SUCESSO!"
echo ">>> Verifique os certificados SSL e logs do Caddy:"
echo "    docker compose -f ${COMPOSE_FILE} logs -f caddy"
echo "======================================================================"

