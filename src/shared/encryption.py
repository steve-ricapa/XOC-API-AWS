import json
import os
from typing import Optional

from cryptography.fernet import Fernet


def get_encryption_key() -> bytes:
    key = os.environ.get("AGENT_KEY_ENCRYPTION_KEY")
    if not key:
        raise ValueError(
            "AGENT_KEY_ENCRYPTION_KEY environment variable is required. "
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
