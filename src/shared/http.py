import json
from typing import Any

from src.shared.errors import AppError


def get_method(event: dict[str, Any]) -> str:
    return ((event.get("requestContext") or {}).get("http") or {}).get("method", "GET").upper()


def get_path(event: dict[str, Any]) -> str:
    return event.get("rawPath") or event.get("path") or "/"


def get_path_parameter(event: dict[str, Any], name: str) -> str | None:
    path_parameters = event.get("pathParameters") or {}
    return path_parameters.get(name)


def get_query_parameter(event: dict[str, Any], name: str, default: Any = None) -> Any:
    query_parameters = event.get("queryStringParameters") or {}
    return query_parameters.get(name, default)


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if body is None or body == "":
        return {}
    if isinstance(body, dict):
        return body
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise AppError("Invalid JSON in request body", status_code=400, code="validation_error") from exc


def get_authorizer_context(event: dict[str, Any]) -> dict[str, Any]:
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    if isinstance(authorizer.get("lambda"), dict):
        return authorizer["lambda"]
    return authorizer if isinstance(authorizer, dict) else {}


def get_bearer_token(event: dict[str, Any]) -> str | None:
    headers = event.get("headers") or {}
    raw_value = headers.get("authorization") or headers.get("Authorization")
    if not raw_value:
        return None
    if raw_value.startswith("Bearer "):
        return raw_value.split(" ", 1)[1].strip()
    return raw_value.strip()
