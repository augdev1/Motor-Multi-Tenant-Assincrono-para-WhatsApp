import hmac
from fastapi import HTTPException, Request, status

from app.config import settings


async def verify_webhook_secret(request: Request) -> None:
    """
    Valida a autenticidade de requisições recebidas no webhook da Evolution API.
    Exige validação via header 'X-Webhook-Secret', 'apikey' ou query param 'token'
    sempre que WEBHOOK_SECRET estiver definido no ambiente.
    """
    expected = (settings.WEBHOOK_SECRET or "").strip()
    if not expected:
        return

    provided = (
        request.headers.get("X-Webhook-Secret")
        or request.headers.get("apikey")
        or request.query_params.get("token")
        or ""
    ).strip()

    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook secret inválido ou ausente.",
        )


async def verify_admin_api_key(request: Request) -> None:
    """
    Protege rotas administrativas (/audit/*, /api/instances/*, /api/companies/*).
    Exige header 'X-Admin-Key' ou 'Authorization: Bearer <token>' quando ADMIN_API_KEY
    estiver definido no ambiente.
    """
    expected = (settings.ADMIN_API_KEY or "").strip()
    if not expected:
        return

    auth_header = request.headers.get("Authorization", "")
    bearer_token = ""
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()

    provided = (request.headers.get("X-Admin-Key") or bearer_token).strip()

    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chave administrativa (ADMIN_API_KEY) inválida ou ausente.",
        )
