"""Asyncio Event Dispatcher / Event Bus.

This is a first-class component (spec §9): it is the single fan-out point that
feeds the frontend live activity feed, the agent network graph, the transaction
timeline, the security center and the audit history.

Design notes
------------
* One process, one event loop, no broker (§9 and §33 forbid Kafka/Redis).
* Each SSE subscriber gets its own bounded ``asyncio.Queue``. A slow consumer drops
  its oldest frames and is told about the gap via a ``BUFFER_OVERFLOW`` notice
  rather than growing without bound or stalling the publisher.
* Every event is persisted to ``event_log`` so ``Last-Event-ID`` reconnects can
  replay missed frames with exact ordering.
* Publishing is synchronous and non-blocking (``put_nowait``) so emitters never
  await a slow HTTP client.

Referenced by: ``app.main``, all routes, agents, security, agreements, blockchain.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.database import SessionLocal

logger = logging.getLogger("trustmesh.events")


class EventType(str, Enum):
    """All events from spec §9 plus the additive observability events from §31."""

    # --- mandated by §9 (exactly 16) ----------------------------------
    AGENT_DISCOVERED = "AGENT_DISCOVERED"
    IDENTITY_VERIFIED = "IDENTITY_VERIFIED"
    IDENTITY_REJECTED = "IDENTITY_REJECTED"
    CAPABILITY_CHECKED = "CAPABILITY_CHECKED"
    OFFER_RECEIVED = "OFFER_RECEIVED"
    NEGOTIATION_STARTED = "NEGOTIATION_STARTED"
    OFFER_UPDATED = "OFFER_UPDATED"
    POLICY_CHECK_STARTED = "POLICY_CHECK_STARTED"
    POLICY_ALLOWED = "POLICY_ALLOWED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    SECURITY_ALERT = "SECURITY_ALERT"
    AGREEMENT_CREATED = "AGREEMENT_CREATED"
    HASH_GENERATED = "HASH_GENERATED"
    BLOCKCHAIN_SUBMITTED = "BLOCKCHAIN_SUBMITTED"
    BLOCKCHAIN_CONFIRMED = "BLOCKCHAIN_CONFIRMED"
    BLOCKCHAIN_FAILED = "BLOCKCHAIN_FAILED"
    # --- additive (§17 three-layer UI, §18 dashboard, §31 observability) -
    INTENT_RECEIVED = "INTENT_RECEIVED"
    TRANSACTION_STATE_CHANGED = "TRANSACTION_STATE_CHANGED"
    AGENT_ACTION_EXECUTED = "AGENT_ACTION_EXECUTED"
    AI_REASONING = "AI_REASONING"
    TRUST_EVALUATED = "TRUST_EVALUATED"
    SYSTEM_READY = "SYSTEM_READY"
    BUFFER_OVERFLOW = "BUFFER_OVERFLOW"


class EventLayer(str, Enum):
    """Which of the three authorities a frame belongs to (spec §17).

    Carrying this on the envelope means the frontend can colour and group the live feed
    by authority without re-deriving it from the event name, which is what makes the
    "AI proposes / TrustMesh decides / Blockchain records" split visible in the UI.
    """

    AGENT = "AGENT"
    TRUSTMESH = "TRUSTMESH"
    BLOCKCHAIN = "BLOCKCHAIN"
    SYSTEM = "SYSTEM"


#: Default layer per event type. ``AI_REASONING`` is the only AGENT-layer frame that is
#: explicitly non-authoritative; the frontend renders it in the AI accent colour.
_LAYER_BY_TYPE: dict[EventType, EventLayer] = {
    EventType.INTENT_RECEIVED: EventLayer.AGENT,
    EventType.AGENT_DISCOVERED: EventLayer.AGENT,
    EventType.OFFER_RECEIVED: EventLayer.AGENT,
    EventType.OFFER_UPDATED: EventLayer.AGENT,
    EventType.NEGOTIATION_STARTED: EventLayer.AGENT,
    EventType.AI_REASONING: EventLayer.AGENT,
    EventType.AGENT_ACTION_EXECUTED: EventLayer.TRUSTMESH,
    EventType.POLICY_CHECK_STARTED: EventLayer.TRUSTMESH,
    EventType.POLICY_ALLOWED: EventLayer.TRUSTMESH,
    EventType.POLICY_BLOCKED: EventLayer.TRUSTMESH,
    EventType.IDENTITY_VERIFIED: EventLayer.TRUSTMESH,
    EventType.IDENTITY_REJECTED: EventLayer.TRUSTMESH,
    EventType.CAPABILITY_CHECKED: EventLayer.TRUSTMESH,
    EventType.SECURITY_ALERT: EventLayer.TRUSTMESH,
    EventType.TRUST_EVALUATED: EventLayer.TRUSTMESH,
    EventType.TRANSACTION_STATE_CHANGED: EventLayer.SYSTEM,
    EventType.AGREEMENT_CREATED: EventLayer.BLOCKCHAIN,
    EventType.HASH_GENERATED: EventLayer.BLOCKCHAIN,
    EventType.BLOCKCHAIN_SUBMITTED: EventLayer.BLOCKCHAIN,
    EventType.BLOCKCHAIN_CONFIRMED: EventLayer.BLOCKCHAIN,
    EventType.BLOCKCHAIN_FAILED: EventLayer.BLOCKCHAIN,
    EventType.SYSTEM_READY: EventLayer.SYSTEM,
    EventType.BUFFER_OVERFLOW: EventLayer.SYSTEM,
}

#: The 16 event types spec §9 requires, in the order it lists them.
MANDATED_EVENT_TYPES: tuple[EventType, ...] = (
    EventType.AGENT_DISCOVERED,
    EventType.IDENTITY_VERIFIED,
    EventType.IDENTITY_REJECTED,
    EventType.CAPABILITY_CHECKED,
    EventType.OFFER_RECEIVED,
    EventType.NEGOTIATION_STARTED,
    EventType.OFFER_UPDATED,
    EventType.POLICY_CHECK_STARTED,
    EventType.POLICY_ALLOWED,
    EventType.POLICY_BLOCKED,
    EventType.SECURITY_ALERT,
    EventType.AGREEMENT_CREATED,
    EventType.HASH_GENERATED,
    EventType.BLOCKCHAIN_SUBMITTED,
    EventType.BLOCKCHAIN_CONFIRMED,
    EventType.BLOCKCHAIN_FAILED,
)

#: Added for §17/§18/§31. Nothing in §9 was removed or renamed.
ADDITIVE_EVENT_TYPES: tuple[EventType, ...] = (
    EventType.INTENT_RECEIVED,
    EventType.TRANSACTION_STATE_CHANGED,
    EventType.AGENT_ACTION_EXECUTED,
    EventType.AI_REASONING,
    EventType.TRUST_EVALUATED,
    EventType.SYSTEM_READY,
    EventType.BUFFER_OVERFLOW,
)


@dataclass(slots=True)
class EventEnvelope:
    """One immutable event frame."""

    seq: int
    type: EventType
    ts: datetime
    transaction_id: str | None = None
    agent_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def layer(self) -> EventLayer:
        """Which authority this frame belongs to. Drives the §17 three-layer UI."""
        return _LAYER_BY_TYPE.get(self.type, EventLayer.SYSTEM)

    @property
    def is_block(self) -> bool:
        """True for frames that represent a denied action."""
        return self.type in _BLOCKING_EVENT_TYPES

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type.value,
            "layer": self.layer.value,
            "ts": self.ts.isoformat(),
            "transaction_id": self.transaction_id,
            "agent_id": self.agent_id,
            "payload": self.payload,
        }

    def to_sse(self) -> str:
        """Serialise to an SSE frame. ``id:`` carries the replay cursor."""
        return (
            f"id: {self.seq}\n"
            f"event: {self.type.value}\n"
            f"data: {json.dumps(self.to_dict(), separators=(',', ':'), default=str)}\n\n"
        )


#: Frames that mean "an action was denied". The Security Center filters on this.
_BLOCKING_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.POLICY_BLOCKED,
        EventType.SECURITY_ALERT,
        EventType.IDENTITY_REJECTED,
        EventType.BLOCKCHAIN_FAILED,
    }
)


class EventDispatcher:
    """In-process publish/subscribe bus with persistence and SSE replay."""

    def __init__(self, buffer_size: int | None = None) -> None:
        self._buffer_size = buffer_size or settings.event_buffer_size
        self._subscribers: dict[int, asyncio.Queue[EventEnvelope]] = {}
        self._history: deque[EventEnvelope] = deque(maxlen=self._buffer_size)
        self._seq = 0
        self._lock = asyncio.Lock()
        self._next_sub_id = 1
        self._started = False
        self._persist_enabled = True
        # Bounded so a stalled database cannot grow memory without limit. The queue is
        # sized well above the SSE buffer because persistence normally keeps up; if it
        # ever does not, dropping the oldest durable rows is better than unbounded growth,
        # and live SSE subscribers are unaffected either way.
        self._persist_queue: asyncio.Queue[EventEnvelope | None] = asyncio.Queue(
            maxsize=max(1_000, self._buffer_size * 4)
        )
        self._persist_task: asyncio.Task | None = None
        self._dropped_persists = 0

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._seq = await asyncio.to_thread(self._load_last_seq)
        # The ring buffer is in-memory only, so a restart begins a fresh replay window.
        self._history.clear()
        self._started = True
        self._ensure_persist_worker()
        logger.info("EventDispatcher started (seq=%s, buffer=%s)", self._seq, self._buffer_size)

    async def stop(self) -> None:
        self._started = False
        task = self._persist_task
        if task is not None:
            self._persist_task = None
            try:
                self._persist_queue.put_nowait(None)
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, RuntimeError):
                # A different (or already closed) event loop may own this task. Dropping
                # it is safe: the queue drains lazily and the log is rebuilt on next boot.
                with contextlib.suppress(RuntimeError):
                    task.cancel()
        for queue in list(self._subscribers.values()):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(
                    EventEnvelope(
                        seq=self._seq,
                        type=EventType.SYSTEM_READY,
                        ts=datetime.now(timezone.utc),
                        payload={"message": "event dispatcher stopped"},
                    )
                )
        logger.info("EventDispatcher stopped")

    async def _persist_worker(self) -> None:
        """Drain the persistence queue on a worker thread.

        Keeping the write off the publisher's await path is essential: an emitter may
        still hold an open write transaction, and writing from another thread inside
        the same await would deadlock on SQLite's single-writer lock.
        """
        while True:
            envelope = await self._persist_queue.get()
            if envelope is None:
                self._persist_queue.task_done()
                return
            try:
                await asyncio.to_thread(self._persist, envelope)
            except Exception:  # pragma: no cover - worker must never die
                logger.exception("event persistence worker error")
            finally:
                self._persist_queue.task_done()

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def drain(self, timeout: float = 5.0) -> bool:
        """Wait until queued events have been written to ``event_log``.

        Persistence is asynchronous by design, so this is only needed by callers that
        read the durable log immediately after publishing (tests, tooling).
        """
        try:
            await asyncio.wait_for(self._persist_queue.join(), timeout=timeout)
            return True
        except asyncio.TimeoutError:  # pragma: no cover
            return False

    def stats(self) -> dict[str, Any]:
        return {
            "running": self._started,
            "seq": self._seq,
            "subscribers": len(self._subscribers),
            "history_size": len(self._history),
            "buffer_size": self._buffer_size,
            "persist_queue_depth": self._persist_queue.qsize(),
            "persist_queue_capacity": self._persist_queue.maxsize,
            "dropped_persists": self._dropped_persists,
        }

    def event_catalogue(self) -> dict[str, Any]:
        """Machine-readable inventory of every event, its layer and its mandate status.

        This is the single source of truth for §9 coverage; the API route and the About
        page both render from it rather than re-declaring the lists.
        """
        return {
            "all": [e.value for e in EventType],
            "mandated_by_specification": [e.value for e in MANDATED_EVENT_TYPES],
            "additive_for_observability": [e.value for e in ADDITIVE_EVENT_TYPES],
            "layers": {e.value: _LAYER_BY_TYPE.get(e, EventLayer.SYSTEM).value for e in EventType},
            "blocking": sorted(e.value for e in _BLOCKING_EVENT_TYPES),
            "bus": self.stats(),
        }

    def _ensure_persist_worker(self) -> bool:
        """Start the persistence worker on demand.

        ``publish`` must not depend on ``start`` having been called first: a caller that
        emits before the lifespan hook runs would otherwise queue events that nothing
        ever drains, silently losing durability. Returns True when a worker is pumping.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False  # no loop: scripts/tests can live without durability
        task = self._persist_task
        if task is not None and not task.done():
            if task.get_loop() is loop:
                return True
            # The task belongs to a loop that has since closed. Drop the reference
            # rather than reusing it; cancelling it can itself raise, hence the guard.
            with contextlib.suppress(RuntimeError):
                task.cancel()
        self._persist_task = loop.create_task(self._persist_worker())
        return True

    # -------------------------------------------------------------- publish
    async def publish(
        self,
        event_type: EventType,
        *,
        transaction_id: str | None = None,
        agent_id: str | None = None,
        payload: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> EventEnvelope:
        """Assign a sequence number, persist and fan out. Never blocks."""
        async with self._lock:
            self._seq += 1
            envelope = EventEnvelope(
                seq=self._seq,
                type=event_type,
                ts=datetime.now(timezone.utc),
                transaction_id=transaction_id,
                agent_id=agent_id,
                payload=payload or {},
            )
            self._history.append(envelope)

        if persist and self._persist_enabled:
            self._ensure_persist_worker()
            # Non-blocking hand-off. Never await the write: the emitter may still hold
            # an open SQLite write transaction.
            if self._persist_queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._persist_queue.get_nowait()
                    self._persist_queue.task_done()
                self._dropped_persists += 1
            with contextlib.suppress(asyncio.QueueFull):  # pragma: no cover - racy
                self._persist_queue.put_nowait(envelope)

        for queue in list(self._subscribers.values()):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    dropped = queue.get_nowait()
                logger.debug("dropping frame seq=%s for slow subscriber", dropped.seq)
                self._overflow_notice(queue, envelope)
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(envelope)
        return envelope

    def _overflow_notice(
        self, queue: asyncio.Queue[EventEnvelope], latest: EventEnvelope
    ) -> None:
        notice = EventEnvelope(
            seq=latest.seq,
            type=EventType.BUFFER_OVERFLOW,
            ts=latest.ts,
            payload={
                "message": "subscriber fell behind; oldest frames dropped",
                "recover": "reconnect with Last-Event-ID or GET /api/events/recent",
            },
        )
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(notice)

    # ----------------------------------------------------------- subscribe
    def subscribe(
        self, last_event_id: int | None = None, *, replay: bool = True
    ) -> tuple[int, asyncio.Queue[EventEnvelope]]:
        """Register a subscriber and optionally enqueue missed frames.

        If the missed window is larger than the ring buffer, only the most recent frames
        fit. That is reported to the client as a ``BUFFER_OVERFLOW`` frame carrying the
        true cursor of the first frame actually delivered, so the UI can tell the user
        that history was truncated instead of silently showing a partial feed.
        """
        sub_id = self._next_sub_id
        self._next_sub_id += 1
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=self._buffer_size)
        self._subscribers[sub_id] = queue

        if replay and last_event_id is not None:
            missed = self.replay_since(last_event_id)
            truncated = len(missed) > self._buffer_size
            if truncated:
                missed = missed[-self._buffer_size :]
                logger.warning(
                    "subscriber %s replay truncated: %s frames buffered, buffer=%s",
                    sub_id,
                    len(missed),
                    self._buffer_size,
                )
                queue.put_nowait(
                    EventEnvelope(
                        seq=self._seq,
                        type=EventType.BUFFER_OVERFLOW,
                        ts=datetime.now(timezone.utc),
                        payload={
                            "message": "replay window exceeded the in-memory buffer",
                            "requested_after_seq": last_event_id,
                            "first_delivered_seq": missed[0].seq if missed else self._seq,
                            "recover": "GET /api/events/recent?since_seq=<seq> for the durable log",
                        },
                    )
                )
            for envelope in missed:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(envelope)
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        self._subscribers.pop(sub_id, None)

    def replay_since(self, since_seq: int) -> list[EventEnvelope]:
        return [env for env in self._history if env.seq > since_seq]

    async def stream(
        self, last_event_id: int | None = None, *, heartbeat: float | None = None
    ) -> AsyncIterator[str]:
        """Async-iterate SSE frames for one HTTP client."""
        interval = heartbeat or settings.event_heartbeat_seconds
        sub_id, queue = self.subscribe(last_event_id)
        try:
            yield f"retry: 3000\n\n"
            while True:
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=interval)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield envelope.to_sse()
        finally:
            self.unsubscribe(sub_id)

    # ----------------------------------------------------------- persistence
    @staticmethod
    def _load_last_seq() -> int:
        from app.core.models import EventLog as EventLogModel

        try:
            with SessionLocal() as session:
                value = session.execute(select(func.max(EventLogModel.seq))).scalar()
                return int(value or 0)
        except Exception:  # pragma: no cover - first boot / missing table
            logger.debug("no prior event log found, starting at seq 0")
            return 0

    @staticmethod
    def _persist(envelope: EventEnvelope) -> None:
        from app.core.models import EventLog as EventLogModel

        try:
            with SessionLocal() as session:
                session.add(
                    EventLogModel(
                        seq=envelope.seq,
                        type=envelope.type.value,
                        transaction_id=envelope.transaction_id,
                        agent_id=envelope.agent_id,
                        payload=envelope.payload,
                    )
                )
                session.commit()
        except Exception:  # pragma: no cover - persistence must never break the bus
            logger.exception("failed to persist event seq=%s", envelope.seq)

    @staticmethod
    def prune_log() -> int:
        """Keep ``event_log`` bounded. Returns the number of rows removed."""
        from app.core.models import EventLog as EventLogModel

        try:
            with SessionLocal() as session:
                total = session.execute(select(func.count(EventLogModel.id))).scalar() or 0
                excess = total - settings.event_persist_limit
                if excess <= 0:
                    return 0
                cutoff = session.execute(
                    select(EventLogModel.seq)
                    .order_by(EventLogModel.seq.asc())
                    .offset(excess - 1)
                    .limit(1)
                ).scalar()
                if cutoff is None:
                    return 0
                result = session.execute(delete(EventLogModel).where(EventLogModel.seq <= cutoff))
                session.commit()
                return int(result.rowcount or 0)
        except Exception:  # pragma: no cover
            logger.exception("failed to prune event log")
            return 0

    def disable_persistence(self) -> None:
        self._persist_enabled = False


dispatcher = EventDispatcher()

__all__ = [
    "ADDITIVE_EVENT_TYPES",
    "EventDispatcher",
    "EventEnvelope",
    "EventLayer",
    "EventType",
    "MANDATED_EVENT_TYPES",
    "dispatcher",
]
