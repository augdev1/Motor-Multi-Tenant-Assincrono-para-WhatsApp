#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE INICIALIZAÇÃO PARA TESTES DIRETOS VIA IP DA VPS (SEM DOMÍNIO/SSL)
# ==============================================================================
# Uso: bash scripts/start_test_ip.sh
# ==============================================================================

set -euo pipefail

COMPOSE_FILE="docker-compose.ip-test.yml"

echo "======================================================================"
echo ">>> INICIANDO STACK EM MODO DE TESTE POR IP DA VPS"
echo "======================================================================"

# 1. Validação ou criação automática do .env caso não exista
if [[ ! -f ".env" ]]; then
    echo "⚠️ Arquivo .env não encontrado. Criando .env básico a partir do template..."
    if [[ -f ".env.prod.example" ]]; then
        cp .env.prod.example .env
    elif [[ -f ".env.example" ]]; then
        cp .env.example .env
    fi
fi

# Detecta o IP público da VPS automaticamente
PUBLIC_IP=$(curl -s https://api.ipify.org || hostname -I | awk '{print $1}')
echo ">>> IP Público Detectado da VPS: ${PUBLIC_IP}"

# 2. Subida e build dos containers
echo ">>> Subindo os containers (${COMPOSE_FILE})..."
docker compose -f "${COMPOSE_FILE}" up -d --build

# 3. Aguarda o PostgreSQL ficar saudável
echo ">>> Aguardando o PostgreSQL inicializar..."
MAX_ATTEMPTS=30
ATTEMPT=1
until [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; do
    STATUS=$(docker compose -f "${COMPOSE_FILE}" ps postgres --format "{{.Health}}" 2>/dev/null || echo "")
    if [[ "$STATUS" == *"healthy"* ]]; then
        echo "✅ PostgreSQL pronto para conexões!"
        break
    fi
    sleep 2
    ATTEMPT=$((ATTEMPT + 1))
done

# 4. Criação de tabelas e catálogo RAG
echo ">>> [1/2] Inicializando tabelas do banco (app.init_db)..."
docker compose -f "${COMPOSE_FILE}" exec -T api python -m app.init_db

echo ">>> [2/2] Inserindo dados do tenant e catálogo RAG (app.seed_lanchonete)..."
docker compose -f "${COMPOSE_FILE}" exec -T api python -m app.seed_lanchonete

# 5. Status final e links de teste
echo "======================================================================"
echo ">>> STATUS DOS CONTAINERS:"
echo "======================================================================"
docker compose -f "${COMPOSE_FILE}" ps

echo ""
echo "======================================================================"
echo ">>> STACK PRONTA PARA TESTES DIRETOS PELO IP!"
echo ">>> Links de acesso no seu navegador:"
echo "    • Swagger / Documentação da API:  http://${PUBLIC_IP}:8000/docs"
echo "    • Health Check da API:            http://${PUBLIC_IP}:8000/health"
echo "    • Painel Evolution API:           http://${PUBLIC_IP}:8080"
echo ""
echo ">>> Para parear o WhatsApp via QR Code agora mesmo:"
echo "    docker compose -f ${COMPOSE_FILE} exec api python scripts/setup_evolution.py \\"
echo "      --instance-name distribuidora_central \\"
echo "      --api-url http://evolution-api:8080 \\"
echo "      --api-key secret-token-123 \\"
echo "      --webhook-url http://api:8000/webhook/evolution"
echo "======================================================================"
