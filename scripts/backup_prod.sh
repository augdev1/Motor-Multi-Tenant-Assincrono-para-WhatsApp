#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE BACKUP AUTOMATIZADO DE PRODUÇÃO (POSTGRESQL + DADOS DE SESSÃO)
# ==============================================================================
# Execução manual: bash scripts/backup_prod.sh
# Agendamento via Cron (diário às 03:00):
# 0 3 * * * /opt/whatsapp-engine/scripts/backup_prod.sh >> /var/log/whatsapp_backup.log 2>&1
# ==============================================================================

set -euo pipefail

PROJECT_DIR="/opt/whatsapp-engine"
cd "${PROJECT_DIR}"

# 1. Carrega variáveis do .env
if [[ -f ".env" ]]; then
    # Exporta apenas variáveis bem formatadas sem quebrar em comentários
    set -a
    source <(grep -v '^#' .env | grep -v '^[[:space:]]*$')
    set +a
else
    echo "❌ Erro: Arquivo .env não localizado em ${PROJECT_DIR}." >&2
    exit 1
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="${PROJECT_DIR}/backups"
BACKUP_FILE="${BACKUP_DIR}/backup_pg_${POSTGRES_DB}_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

echo ">>> [$(date)] Iniciando backup do banco de dados ${POSTGRES_DB}..."

# 2. Executa o pg_dump em streaming comprimindo com gzip
docker compose -f docker-compose.prod.yml exec -T postgres \
    pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --clean --if-exists | gzip > "${BACKUP_FILE}"

BACKUP_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
echo "✅ Backup do PostgreSQL gerado com sucesso: ${BACKUP_FILE} (${BACKUP_SIZE})"

# 3. Executa o backup das sessões e tokens de autenticação da Evolution API
SESSION_BACKUP_FILE="${BACKUP_DIR}/backup_evolution_sessions_${TIMESTAMP}.tar.gz"
echo ">>> [$(date)] Realizando backup persistente dos tokens de sessão WhatsApp (/evolution/instances)..."
docker compose -f docker-compose.prod.yml exec -T evolution-api tar -czf - /evolution/instances /evolution/store 2>/dev/null > "${SESSION_BACKUP_FILE}" || true

if [[ -f "${SESSION_BACKUP_FILE}" && -s "${SESSION_BACKUP_FILE}" ]]; then
    SESSION_SIZE=$(du -h "${SESSION_BACKUP_FILE}" | cut -f1)
    echo "✅ Backup das sessões Evolution gerado com sucesso: ${SESSION_BACKUP_FILE} (${SESSION_SIZE})"
else
    echo "⚠️ Aviso: Volume de instâncias ainda vazio ou não exportado nesta rotina."
fi

# 4. Política de Retenção: Remove backups com mais de 7 dias
echo ">>> Excluindo backups com mais de 7 dias de retenção..."
find "${BACKUP_DIR}" -type f -name "backup_pg_*.sql.gz" -mtime +7 -delete
find "${BACKUP_DIR}" -type f -name "backup_evolution_sessions_*.tar.gz" -mtime +7 -delete

echo ">>> [$(date)] Rotina de backup finalizada com sucesso."

