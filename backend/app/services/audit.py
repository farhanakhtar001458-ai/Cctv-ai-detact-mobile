"""Administrative audit trail."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, select

from app.core.logging import redact
from app.db.models import AuditLog
from app.db.session import SessionLocal

log = logging.getLogger(__name__)


def record(action: str, actor: str = "system", target: str = "", detail: str = "", ip: str = "") -> None:
    detail = redact(str(detail))[:4000]
    try:
        with SessionLocal() as db:
            db.add(AuditLog(actor=actor[:64], action=action[:64], target=target[:128], detail=detail, ip_address=ip[:64]))
            db.commit()
    except Exception as exc:  # noqa: BLE001 - auditing must never break the request
        log.warning("Failed to write audit log for %s: %s", action, exc)


def list_logs(limit: int = 100, action: str | None = None, actor: str | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        stmt = select(AuditLog).order_by(desc(AuditLog.created_at)).limit(min(limit, 500))
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if actor:
            stmt = stmt.where(AuditLog.actor == actor)
        rows = db.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat(),
                "actor": r.actor,
                "action": r.action,
                "target": r.target,
                "detail": r.detail,
                "ip_address": r.ip_address,
            }
            for r in rows
        ]
