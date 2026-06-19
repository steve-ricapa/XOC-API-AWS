from collections.abc import Callable
from typing import Any

from src.shared.errors import AppError
from src.shared.logging import logger
from src.shared.responses import json_response


def handle_errors(func: Callable[[dict[str, Any], Any], dict[str, Any]]) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    def wrapper(event: dict[str, Any], context: Any) -> dict[str, Any]:
        try:
            return func(event, context)
        except AppError as exc:
            logger.warning("Application error", extra={"code": exc.code, "message": exc.message})
            return json_response(exc.status_code, {"error": exc.message, "code": exc.code})
        except Exception:
            logger.exception("Unhandled error")
            return json_response(500, {"error": "Internal server error", "code": "internal_error"})

    return wrapper
