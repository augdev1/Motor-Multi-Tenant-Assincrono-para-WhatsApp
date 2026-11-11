FROM python:3.12-slim

# Variáveis de ambiente Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on

# Diretório de trabalho
WORKDIR /app

# Instalar uv diretamente da imagem oficial
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copiar arquivos de definição de dependências e migrações
COPY pyproject.toml README.md alembic.ini ./
COPY alembic/ ./alembic/

# Instalar dependências da aplicação usando wheels binários pré-compilados
RUN uv pip install --system -e .

# Copiar código fonte
COPY app/ ./app/

# Expor porta da API
EXPOSE 8000

# Execução do servidor ASGI Uvicorn com múltiplos workers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
