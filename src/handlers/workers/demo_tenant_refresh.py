from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import select

from scripts.seed_demo_tenant_data import seed_tenant
from src.persistence.db import session_scope
from src.persistence.models import Tenant


def _current_refresh_seed(window_hours: int) -> int:
    now = datetime.now(timezone.utc)
    bucket = now.hour // max(1, window_hours)
    return int(now.strftime("%Y%m%d")) * 10 + bucket


def handler(event, context):
    demo_tenant_name = os.environ.get("DEMO_TENANT_NAME", "XOC Demo").strip()
    demo_tenant_id_raw = (os.environ.get("DEMO_TENANT_ID") or "").strip()
    window_hours = int(os.environ.get("DEMO_REFRESH_WINDOW_HOURS", "6"))

    tenant_id = None
    with session_scope() as session:
        if demo_tenant_id_raw:
            tenant = session.get(Tenant, int(demo_tenant_id_raw))
        else:
            tenant = session.scalar(select(Tenant).where(Tenant.name == demo_tenant_name))
        if not tenant:
            raise ValueError(f"Demo tenant not found: {demo_tenant_name or demo_tenant_id_raw}")
        tenant_id = int(tenant.id)

    seed = _current_refresh_seed(window_hours)
    seed_tenant(tenant_id, random_seed=seed)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "tenant_name": demo_tenant_name,
        "seed": seed,
        "window_hours": window_hours,
    }
