import secrets
import string

import bcrypt


def generate_access_key(length: int = 40) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_access_key(access_key: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(access_key.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_access_key(access_key: str, hashed_key: str) -> bool:
    return bcrypt.checkpw(access_key.encode("utf-8"), hashed_key.encode("utf-8"))
