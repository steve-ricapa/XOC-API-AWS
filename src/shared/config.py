import json
import os
from dataclasses import dataclass
from functools import lru_cache

import boto3

from src.shared.errors import ConfigurationError


@dataclass(frozen=True)
class Settings:
    app_stage: str
    app_region: str
    jwt_secret_key_ssm_path: str | None
    jwt_secret_arn: str | None
    database_secret_arn: str | None
    database_url_ssm_path: str | None
    snapshots_bucket_name: str | None
    cors_allowed_origins: list[str]
    event_bus_name: str | None
    agents_function_base_url: str | None
    agents_function_route_sophia: str | None
    agents_function_route_sophia_history: str | None
    agents_function_route_sophia_delete: str | None
    agents_function_route_victor: str | None
    cases_table_name: str | None
    enable_api_docs: bool
    public_registration_enabled: bool


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    app_stage = os.environ.get("APP_STAGE", "dev")
    return Settings(
        app_stage=app_stage,
        app_region=os.environ.get("APP_REGION", "us-east-1"),
        jwt_secret_key_ssm_path=os.environ.get("JWT_SECRET_KEY_SSM_PATH"),
        jwt_secret_arn=os.environ.get("JWT_SECRET_ARN"),
        database_secret_arn=os.environ.get("DATABASE_SECRET_ARN"),
        database_url_ssm_path=os.environ.get("DATABASE_URL_SSM_PATH"),
        snapshots_bucket_name=os.environ.get("SNAPSHOTS_BUCKET_NAME"),
        cors_allowed_origins=_split_csv(os.environ.get("CORS_ALLOWED_ORIGINS")),
        event_bus_name=os.environ.get("EVENT_BUS_NAME"),
        agents_function_base_url=os.environ.get("AGENTS_FUNCTION_BASE_URL"),
        agents_function_route_sophia=os.environ.get("AGENTS_FUNCTION_ROUTE_SOPHIA"),
        agents_function_route_sophia_history=os.environ.get("AGENTS_FUNCTION_ROUTE_SOPHIA_HISTORY"),
        agents_function_route_sophia_delete=os.environ.get("AGENTS_FUNCTION_ROUTE_SOPHIA_DELETE"),
        agents_function_route_victor=os.environ.get("AGENTS_FUNCTION_ROUTE_VICTOR"),
        cases_table_name=os.environ.get("CASES_TABLE_NAME"),
        enable_api_docs=app_stage not in {"prod"},
        public_registration_enabled=os.environ.get("PUBLIC_REGISTRATION_ENABLED", "true").strip().lower() in ("true", "1", "yes"),
    )


@lru_cache(maxsize=8)
def get_ssm_parameter(parameter_name: str, *, decrypt: bool = True) -> str:
    client = boto3.client("ssm")
    response = client.get_parameter(Name=parameter_name, WithDecryption=decrypt)
    value = response.get("Parameter", {}).get("Value")
    if not value:
        raise ConfigurationError(f"SSM parameter is empty: {parameter_name}")
    return value


@lru_cache(maxsize=8)
def get_secret_string(secret_arn: str) -> str:
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    secret_string = response.get("SecretString")
    if not secret_string:
        raise ConfigurationError(f"SecretString is empty: {secret_arn}")
    return secret_string


def get_database_url() -> str | None:
    direct_url = os.environ.get("DATABASE_URL")
    if direct_url:
        return direct_url

    settings = get_settings()
    if settings.database_url_ssm_path:
        return get_ssm_parameter(settings.database_url_ssm_path)

    if settings.database_secret_arn:
        secret_string = get_secret_string(settings.database_secret_arn)
        try:
            payload = json.loads(secret_string)
        except json.JSONDecodeError:
            return secret_string
        return payload.get("database_url") or payload.get("DATABASE_URL")

    return None


def get_jwt_secret_key() -> str:
    settings = get_settings()

    if settings.jwt_secret_arn:
        secret = get_secret_string(settings.jwt_secret_arn)
        try:
            payload = json.loads(secret)
            return payload.get("secret") or secret
        except json.JSONDecodeError:
            return secret

    direct_secret = os.environ.get("JWT_SECRET_KEY")
    if direct_secret:
        return direct_secret

    if settings.jwt_secret_key_ssm_path:
        return get_ssm_parameter(settings.jwt_secret_key_ssm_path)

    raise ConfigurationError("JWT secret source is not configured")


def get_snapshots_bucket_name() -> str:
    bucket_name = get_settings().snapshots_bucket_name
    if not bucket_name:
        raise ConfigurationError("Snapshots bucket is not configured")
    return bucket_name
