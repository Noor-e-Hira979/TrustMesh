"""Event stream routes - SSE plus a REST fallback.

SSE contract:
* ``text/event-stream`` with ``id:`` = monotonic sequence number.
* ``Last-Event-ID`` request header (or ``?since_seq=``) triggers replay of missed
  frames from the in-memory ring buffer.
* A ``: heartbeat`` comment frame every N seconds keeps proxies from closing the
  connection during the demo.
* ``retry:`` tells the browser how fast to reconnect.

The browser's native ``EventSource`` sends ``Last-Event-ID`` automatically on
reconnect, so a page refresh mid-demo resumes instead of restarting.

Referenced by: ``app.main``, ``frontend/src/hooks/useEventStream.js``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.config import settings
from app.core.database import run_db
from app.core.events import dispatcher
from app.core.models import EventLog
from app.core.schemas import BusStatsOut, EventFeedOut, EventTypesOut

router = APIRouter(tags=["events"])


@router.get("/events/stream", summary="Server-Sent Events live stream")
async def stream(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    since_seq: int | None = Query(default=None, ge=0),
    heartbeat: float | None = Query(default=None, ge=1.0, le=120.0),
) -> StreamingResponse:
    cursor: int | None = None
    if last_event_id and last_event_id.isdigit():
        cursor = int(last_event_id)
    elif since_seq is not None:
        cursor = since_seq

    async def generator():
        try:
            async for frame in dispatcher.stream(cursor, heartbeat=heartbeat):
                if await request.is_disconnected():
                    break
                yield frame
        except asyncio.CancelledError:  # pragma: no cover - client hang-up
            raise

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/events/recent",
    response_model=EventFeedOut,
    summary="Event feed fallback / hydration (durable log, written asynchronously)",
)
async def recent(
    since_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=2000),
    event_type: str | None = Query(default=None),
) -> dict:
    def _query(session):
        stmt = select(EventLog).where(EventLog.seq > since_seq)
        if event_type:
            stmt = stmt.where(EventLog.type == event_type)
        rows = list(
            session.execute(stmt.order_by(EventLog.seq.asc()).limit(limit)).scalars().all()
        )
        return {
            "events": [
                {
                    "seq": row.seq,
                    "type": row.type,
                    "ts": row.created_at.isoformat() if row.created_at else None,
                    "transaction_id": row.transaction_id,
                    "agent_id": row.agent_id,
                    "payload": row.payload,
                }
                for row in rows
            ],
            "count": len(rows),
            "latest_seq": dispatcher.stats()["seq"],
        }

    return await run_db(_query)


@router.get("/events/types", response_model=EventTypesOut, summary="Every event type the bus can emit")
async def event_types() -> dict:
    """Renders from ``EventDispatcher.event_catalogue`` so §9 coverage has one owner."""
    return dispatcher.event_catalogue()


@router.get("/events/bus", response_model=BusStatsOut, summary="Bus statistics")
async def bus_stats() -> dict:
    return {
        **dispatcher.stats(),
        "heartbeat_seconds": settings.event_heartbeat_seconds,
        "observed_at": datetime.now(timezone.utc),
    }
