import json
from typing import Any


def json_response(status_code: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    base_headers = {
        "Content-Type": "application/json",
    }
    if headers:
        base_headers.update(headers)
    return {
        "statusCode": status_code,
        "headers": base_headers,
        "body": json.dumps(body),
    }
