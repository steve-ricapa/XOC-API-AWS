import base64
import json
import os
from typing import Optional

from cryptography.fernet import Fernet

from src.shared.config import get_secret_string


def get_encryption_key() -> bytes:
    arn = os.environ.get("AGENT_KEY_ENCRYPTION_KEY_ARN")
    if arn:
        secret = get_secret_string(arn)
        try:
            payload = json.loads(secret)
            raw = payload.get("key") or secret
        except json.JSONDecodeError:
            raw = secret
        return base64.urlsafe_b64encode(raw.encode("utf-8")[:32])

    key = os.environ.get("AGENT_KEY_ENCRYPTION_KEY")
    if not key:
        raise ValueError(
            "AGENT_KEY_ENCRYPTION_KEY or AGENT_KEY_ENCRYPTION_KEY_ARN is required. "
            "Generate one with Fernet.generate_key() for local use."
        )
    return key.encode("utf-8") if isinstance(key, str) else key


def encrypt_credentials(credentials_dict: dict) -> str:
    fernet = Fernet(get_encryption_key())
    return fernet.encrypt(json.dumps(credentials_dict).encode("utf-8")).decode("utf-8")


def decrypt_credentials(encrypted_credentials: str) -> Optional[dict]:
    try:
        fernet = Fernet(get_encryption_key())
        decrypted = fernet.decrypt(encrypted_credentials.encode("utf-8"))
        return json.loads(decrypted.decode("utf-8"))
    except Exception:
        return None


def encrypt_agent_key(key: str) -> str:
    fernet = Fernet(get_encryption_key())
    return fernet.encrypt(key.encode("utf-8")).decode("utf-8")


def decrypt_agent_key(encrypted_key: str) -> Optional[str]:
    try:
        fernet = Fernet(get_encryption_key())
        decrypted = fernet.decrypt(encrypted_key.encode("utf-8"))
        return decrypted.decode("utf-8")
    except Exception:
        return None
