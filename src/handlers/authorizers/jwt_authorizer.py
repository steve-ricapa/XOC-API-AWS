from aws_lambda_powertools.utilities.typing import LambdaContext
import jwt

from src.shared.config import get_jwt_secret_key
from src.shared.logging import logger


def _extract_token(event: dict) -> str | None:
    identity_sources = event.get("identitySource") or []
    if identity_sources:
        raw_value = identity_sources[0]
    else:
        headers = event.get("headers") or {}
        raw_value = headers.get("authorization") or headers.get("Authorization")

    if not raw_value:
        return None

    if raw_value.startswith("Bearer "):
        return raw_value.split(" ", 1)[1].strip()

    return raw_value.strip()


@logger.inject_lambda_context(log_event=False)
def handler(event: dict, context: LambdaContext) -> dict:
    token = _extract_token(event)
    if not token:
        return {"isAuthorized": False}

    try:
        payload = jwt.decode(
            token,
            get_jwt_secret_key(),
            algorithms=["HS256"],
            options={"require": []},
        )
    except Exception as exc:
        logger.warning("JWT validation failed", extra={"error": str(exc)})
        return {"isAuthorized": False}

    context_payload = {
        "userId": str(payload.get("sub") or payload.get("identity") or ""),
        "companyId": str(payload.get("company_id") or ""),
        "role": str(payload.get("role") or ""),
        "tokenType": str(payload.get("type") or ""),
        "scopes": payload.get("scopes") or [],
        "agentType": str(payload.get("agent_type") or ""),
    }
    return {
        "isAuthorized": True,
        "context": context_payload,
    }
