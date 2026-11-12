from typing import Optional
from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações centralizadas da aplicação."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "whatsapp_engine"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    DATABASE_URL: Optional[str] = None

    REDIS_URL: str = "redis://localhost:6379/0"

    EVOLUTION_API_URL: str = "http://localhost:8080"
    EVOLUTION_API_KEY: str = "secret-token"

    GEMINI_API_KEY: str = "mock-key"

    # Segurança de borda e autenticação
    WEBHOOK_SECRET: str = ""
    ADMIN_API_KEY: str = ""

    # Escala assíncrona e alertas ativos
    ASYNC_QUEUE_ENABLED: bool = False
    ALERT_WEBHOOK_URL: Optional[str] = None
    USE_REAL_EMBEDDINGS: bool = True

    # Proteção anti-banimento e simulação de comportamento humano
    SIMULATE_HUMAN_BEHAVIOR: bool = True
    HUMAN_MIN_TYPING_DELAY: float = 1.2
    HUMAN_MAX_TYPING_DELAY: float = 4.0
    DEBOUNCE_ENABLED: bool = True
    DEBOUNCE_WAIT_SECONDS: float = 3.0

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    @computed_field  # type: ignore[prop-decorator]
    @property
    def async_postgres_url(self) -> str:
        """Gera a URL de conexão assíncrona com PostgreSQL via asyncpg."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()

