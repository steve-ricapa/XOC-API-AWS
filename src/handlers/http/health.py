from aws_lambda_powertools.utilities.typing import LambdaContext

from src.persistence.db import is_database_available
from src.shared.config import get_settings
from src.shared.logging import logger
from src.shared.responses import json_response


@logger.inject_lambda_context(log_event=True)
def handler(event: dict, context: LambdaContext) -> dict:
    settings = get_settings()
    db_available = is_database_available()

    return json_response(
        200,
        {
            "status": "healthy",
            "service": "xoc-api-core",
            "stage": settings.app_stage,
            "database": "available" if db_available else "unavailable",
        },
        headers={
            "X-Request-Id": getattr(context, "aws_request_id", "unknown"),
        },
    )
