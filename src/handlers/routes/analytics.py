from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import Alert, User, Vulnerability
from src.shared.context import log_audit
from src.shared.dependencies import get_current_user
from src.shared.tickets_store import count_tenant_tickets


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/incidents")
def get_incidents_analytics(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session), hours: int = 24) -> dict:
    time_threshold = datetime.utcnow() - timedelta(hours=hours)
    alerts = session.scalars(select(Alert).where(Alert.tenant_id == current_user.tenant_id, Alert.created_at >= time_threshold)).all()
    severity_count = {
        "critical": len([alert for alert in alerts if alert.severity == "critical"]),
        "high": len([alert for alert in alerts if alert.severity == "high"]),
        "medium": len([alert for alert in alerts if alert.severity == "medium"]),
        "low": len([alert for alert in alerts if alert.severity == "low"]),
    }
    log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="ANALYTICS_INCIDENTS", payload=severity_count)
    session.commit()
    return {
        "period_hours": hours,
        "total_incidents": len(alerts),
        "by_severity": severity_count,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/response-time")
def get_response_time_analytics(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session), days: int = 7) -> dict:
    time_threshold = datetime.utcnow() - timedelta(days=days)
    resolved_alerts = session.scalars(select(Alert).where(Alert.tenant_id == current_user.tenant_id, Alert.status == "resolved", Alert.created_at >= time_threshold)).all()
    response_times = []
    for alert in resolved_alerts:
        if alert.resolved_at and alert.created_at:
            response_times.append((alert.resolved_at - alert.created_at).total_seconds() / 60)
    avg_response_time = sum(response_times) / len(response_times) if response_times else 0
    log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="ANALYTICS_RESPONSE_TIME", payload={"avg": avg_response_time})
    session.commit()
    return {
        "period_days": days,
        "average_response_time_minutes": round(avg_response_time, 2),
        "total_resolved_alerts": len(resolved_alerts),
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/vulnerability-distribution")
def get_vulnerability_distribution(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    vulnerabilities = session.scalars(select(Vulnerability).where(Vulnerability.tenant_id == current_user.tenant_id, Vulnerability.status == "open")).all()
    severity_distribution = {
        "critical": len([v for v in vulnerabilities if v.severity == "critical"]),
        "high": len([v for v in vulnerabilities if v.severity == "high"]),
        "medium": len([v for v in vulnerabilities if v.severity == "medium"]),
        "low": len([v for v in vulnerabilities if v.severity == "low"]),
    }
    cvss_ranges = {
        "9.0-10.0": len([v for v in vulnerabilities if v.cvss_score and v.cvss_score >= 9.0]),
        "7.0-8.9": len([v for v in vulnerabilities if v.cvss_score and 7.0 <= v.cvss_score < 9.0]),
        "4.0-6.9": len([v for v in vulnerabilities if v.cvss_score and 4.0 <= v.cvss_score < 7.0]),
        "0.1-3.9": len([v for v in vulnerabilities if v.cvss_score and 0.1 <= v.cvss_score < 4.0]),
    }
    log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="ANALYTICS_VULNERABILITIES", payload=severity_distribution)
    session.commit()
    return {
        "total_active_vulnerabilities": len(vulnerabilities),
        "by_severity": severity_distribution,
        "by_cvss_range": cvss_ranges,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/summary")
def get_analytics_summary(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    active_alerts = session.query(Alert).filter_by(tenant_id=current_user.tenant_id, status="active").count()
    open_vulnerabilities = session.query(Vulnerability).filter_by(tenant_id=current_user.tenant_id, status="open").count()
    pending_tickets = count_tenant_tickets(current_user.tenant_id, status="PENDING")
    return {
        "active_alerts": active_alerts,
        "open_vulnerabilities": open_vulnerabilities,
        "pending_tickets": pending_tickets,
        "timestamp": datetime.utcnow().isoformat(),
    }
