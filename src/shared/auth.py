from datetime import datetime, timedelta, timezone

import jwt

from src.shared.config import get_jwt_secret_key
from src.shared.errors import UnauthorizedError


ACCESS_TOKEN_MINUTES = 5
REFRESH_TOKEN_DAYS = 30


def _build_claims(*, identity: str, token_type: str, additional_claims: dict | None = None, expires_delta: timedelta | None = None) -> dict:
    now = datetime.now(timezone.utc)
    expires_at = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_MINUTES))
    payload = {
        "sub": identity,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if additional_claims:
        payload.update(additional_claims)
    return payload


def create_access_token(*, identity: str, additional_claims: dict | None = None, expires_delta: timedelta | None = None) -> str:
    payload = _build_claims(
        identity=identity,
        token_type="access",
        additional_claims=additional_claims,
        expires_delta=expires_delta or timedelta(minutes=ACCESS_TOKEN_MINUTES),
    )
    return jwt.encode(payload, get_jwt_secret_key(), algorithm="HS256")


def create_refresh_token(*, identity: str) -> str:
    payload = _build_claims(
        identity=identity,
        token_type="refresh",
        expires_delta=timedelta(days=REFRESH_TOKEN_DAYS),
    )
    return jwt.encode(payload, get_jwt_secret_key(), algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_jwt_secret_key(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid token") from exc
