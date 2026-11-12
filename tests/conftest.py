import pytest
from app.config import settings


@pytest.fixture(scope="session", autouse=True)
def configure_offline_test_defaults():
    """Mantém a suíte de testes 100% determinística e offline por padrão."""
    settings.USE_REAL_EMBEDDINGS = False
    settings.SIMULATE_HUMAN_BEHAVIOR = False
    settings.DEBOUNCE_ENABLED = False
    yield

