"""Detection event queries, manual review and CSV export."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.schemas import AcknowledgeRequest, EventOut, MessageOut
from app.core.rate_limit import enforce
from app.core.security import SupervisorUser, ViewerUser
from app.db.models import DetectionEvent, EventCategory
from app.db.session import get_db
from app.services.audit import record as audit
from app.services.events import acknowledge_event, event_to_dict, list_events
from app.services.stats import events_to_csv

router = APIRouter(prefix="/api/events", tags=["events"])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid datetime: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@router.get("", response_model=list[EventOut])
def get_events(
    user: ViewerUser,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    camera_id: int | None = None,
    category: str | None = Query(default=None, description="person_detected|phone_detected|possible_phone_use|sustained_phone_use|uncertain"),
    source: str | None = Query(default=None, description="model|simulated"),
    tracking_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[EventOut]:
    if category and category not in {c.value for c in EventCategory}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown category '{category}'")
    events = list_events(
        limit=limit,
        offset=offset,
        camera_id=camera_id,
        category=category,
        source=source,
        tracking_id=tracking_id,
        since=_parse_dt(since),
        until=_parse_dt(until),
    )
    return [EventOut(**event) for event in events]


@router.get("/categories")
def categories(user: ViewerUser) -> list[dict[str, str]]:
    labels = {
        EventCategory.PERSON_DETECTED: "Person detected",
        EventCategory.PHONE_DETECTED: "Mobile phone detected",
        EventCategory.POSSIBLE_PHONE_USE: "Possible phone use",
        EventCategory.SUSTAINED_PHONE_USE: "Sustained phone-use alert",
        EventCategory.UNCERTAIN: "Uncertain detection",
    }
    return [{"value": c.value, "label": labels[c]} for c in EventCategory]


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: int, user: ViewerUser, db: Annotated[Session, Depends(get_db)]) -> EventOut:
    row = db.get(DetectionEvent, event_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return EventOut(**event_to_dict(row))


@router.post("/{event_id}/acknowledge", response_model=MessageOut)
def acknowledge(
    event_id: int,
    payload: AcknowledgeRequest,
    user: SupervisorUser,
    request: Request,
) -> MessageOut:
    enforce(request, "event_ack", "60/minute")
    result = acknowledge_event(event_id, user.username, payload.note)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    audit("event_acknowledged", actor=user.username, target=str(event_id), detail=payload.note[:300])
    return MessageOut(message="Event reviewed", detail=result)


@router.get("/export/csv")
def export_csv(
    user: SupervisorUser,
    request: Request,
    camera_id: int | None = None,
    category: str | None = None,
    source: str | None = None,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=5000, ge=1, le=50000),
) -> Response:
    enforce(request, "export", "10/minute")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    events = list_events(limit=limit, camera_id=camera_id, category=category, source=source, since=since)
    csv_text = events_to_csv(events)
    audit("events_exported", actor=user.username, detail=f"{len(events)} rows; days={days}")
    filename = f"classroom_events_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/purge", response_model=MessageOut)
def purge(user: SupervisorUser, older_than_days: int | None = None) -> MessageOut:
    """Apply the retention policy manually (also runs nightly)."""
    from app.services.events import purge_expired

    result = purge_expired(older_than_days)
    audit("events_purged", actor=user.username, detail=str(result))
    return MessageOut(message="Retention policy applied", detail=result)
