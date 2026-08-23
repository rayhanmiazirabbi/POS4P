from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.sync import Device, DeviceStatus, StoreSequence, SyncCheckpoint, SyncEvent, SyncFeedItem
from app.errors import Conflict, DomainError, Forbidden, NotFound
from app.models import Role
from app.schemas.sync import SyncAck
from app.security import utc_now
from app.services.audit import record_audit

#: Anyone who may ring up a sale may upload one they rang up offline. Restricting
#: this to owners and managers would strand every cashier's queue the moment the
#: connection dropped, which is the one case the queue exists for.
INGEST_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.CASHIER})

#: Dispatch table: event_type -> async handler(session, context, device, envelope).
#: Domain teams register handlers for their own event types; unknown types are
#: rejected at ingest time so clients never mutate entities directly.
HANDLERS: dict[str, Callable[[AsyncSession, RequestContext, Device, Any], Awaitable[None]]] = {}


def register_handler(
    event_type: str,
    handler: Callable[[AsyncSession, RequestContext, Device, Any], Awaitable[None]],
) -> None:
    """Register an ingest handler.

    A handler **must be idempotent on ``envelope.event_id``**. Ingest retries an
    event whose bookkeeping did not finish -- a crash between the handler's own
    commit and the feed write leaves exactly that state -- so a handler that applies
    its effect twice will double-book. Deriving the domain idempotency key from the
    event id, as ``_handle_sale_create`` does, satisfies this.
    """
    HANDLERS[event_type] = handler


async def _handle_ping(
    session: AsyncSession, context: RequestContext, device: Device, envelope: Any
) -> None:
    return None


register_handler("ping", _handle_ping)


async def _handle_sale_create(
    session: AsyncSession, context: RequestContext, device: Device, envelope: Any
) -> None:
    """Replay an offline POS sale through the real sales command.

    The envelope id doubles as the idempotency key, so a device retry after a
    crashed upload can never book the same sale twice. The sale's outbox rows
    are suppressed because the ingest passthrough feed item already represents
    the change for pulling devices.
    """
    from uuid import uuid4

    from pydantic import ValidationError as PydanticValidationError

    from app.errors import ValidationError
    from app.models import OutboxEvent
    from app.schemas.sales import SaleCreateRequest
    from app.services.sales import create_sale

    try:
        payload = SaleCreateRequest.model_validate(envelope.payload)
    except PydanticValidationError as exc:
        raise ValidationError(
            f"Invalid sale.create payload: {exc.error_count()} field errors"
        ) from exc
    result = await create_sale(
        session,
        context,
        payload,
        idempotency_key=f"offline:{envelope.event_id}",
        request_id=str(uuid4()),
    )
    if result.replay_body is None:
        echoes = list(
            await session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.organization_id == context.organization_id,
                    OutboxEvent.aggregate_id == result.sale.id,
                    OutboxEvent.published_at.is_(None),
                )
            )
        )
        for echo in echoes:
            echo.published_at = utc_now()
        if echoes:
            await session.commit()


register_handler("sale.create", _handle_sale_create)


async def register_device(
    session: AsyncSession,
    context: RequestContext,
    name: str,
    device_key: str,
) -> Device:
    if context.store_id is None:
        raise NotFound("Store not found")
    device = Device(
        organization_id=context.organization_id,
        store_id=context.store_id,
        name=name.strip(),
        device_key=device_key.strip(),
        status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Device key already registered") from exc
    return device


async def list_devices(session: AsyncSession, context: RequestContext) -> list[Device]:
    scope = (Device.organization_id == context.organization_id,)
    if context.store_id is not None:
        scope = (*scope, Device.store_id == context.store_id)
    return list(
        await session.scalars(select(Device).where(*scope).order_by(Device.created_at, Device.id))
    )


async def revoke_device(
    session: AsyncSession,
    context: RequestContext,
    device_id: UUID,
    *,
    request_id: str,
    reason: str | None = None,
) -> Device:
    """Revoke a terminal immediately; its token claim is re-checked per request.

    ``reason`` is recorded because revocation is usually an incident -- a lost or
    stolen till -- and the audit row is the only place that context survives.
    """
    device = await session.get(Device, device_id)
    if (
        device is None
        or device.organization_id != context.organization_id
        or (context.store_id is not None and device.store_id != context.store_id)
    ):
        raise NotFound("Device not found")
    device.status = DeviceStatus.REVOKED
    record_audit(
        session,
        context,
        action="sync.device.revoked",
        entity_type="device",
        entity_id=device.id,
        request_id=request_id,
        after={"device_key": device.device_key, "reason": reason},
    )
    return device


async def load_active_device(session: AsyncSession, context: RequestContext) -> Device:
    """Resolve the caller's device from the ``dev`` token claim; must be ACTIVE."""
    if context.device_id is None:
        from app.errors import ValidationError

        raise ValidationError("Device context required", code="DEVICE_CONTEXT_REQUIRED")
    device = await session.get(Device, context.device_id)
    if (
        device is None
        or device.organization_id != context.organization_id
        or device.store_id != context.store_id
        or device.status is not DeviceStatus.ACTIVE
    ):
        raise Forbidden("Device access denied")
    return device


async def _next_server_sequence(session: AsyncSession, store_id: UUID) -> int:
    """Allocate the next feed sequence for a store, under a row lock.

    Two devices uploading at once would otherwise both read the same value and
    write two feed items claiming one sequence, which the unique constraint on
    ``(store_id, server_sequence)`` turns into a failed upload -- and, before that
    constraint existed, into a change some devices would never pull because the
    cursor skipped straight past it.
    """
    counter = await session.get(StoreSequence, store_id, with_for_update=True)
    if counter is None:
        counter = StoreSequence(store_id=store_id, last_sequence=0, last_receipt_sequence=0)
        session.add(counter)
        try:
            await session.flush()
        except IntegrityError:
            # Another request created the row first; take theirs, locked.
            await session.rollback()
            counter = await session.get(StoreSequence, store_id, with_for_update=True)
    if counter is None:  # pragma: no cover - the row exists by now or the insert raised
        raise Conflict("Could not allocate a server sequence")
    counter.last_sequence += 1
    return counter.last_sequence


def _verify_envelope_identity(
    envelope: Any, context: RequestContext, device: Device
) -> str | None:
    """Check the rule-5 identity fields against the token; return an error code.

    The envelope carries these so an offline queue entry is self-describing, but a
    device could send anything. Where a value is present it must agree with the
    authenticated context -- otherwise a compromised terminal could file its sales
    against another branch, which would move stock and revenue between shops that
    do not share an owner.

    Absent fields are accepted: older clients do not send them, and the token
    already supplies the authoritative values.
    """
    mismatches = (
        envelope.organization_id is not None
        and envelope.organization_id != context.organization_id,
        envelope.store_id is not None and envelope.store_id != device.store_id,
        envelope.device_id is not None and envelope.device_id != device.id,
        envelope.user_id is not None and envelope.user_id != context.user_id,
    )
    return "IDENTITY_MISMATCH" if any(mismatches) else None


async def _checkpoint(
    session: AsyncSession, organization_id: UUID, store_id: UUID, device_id: UUID
) -> SyncCheckpoint:
    checkpoint = await session.scalar(
        select(SyncCheckpoint).where(
            SyncCheckpoint.store_id == store_id, SyncCheckpoint.device_id == device_id
        )
    )
    if checkpoint is None:
        checkpoint = SyncCheckpoint(
            organization_id=organization_id, store_id=store_id, device_id=device_id
        )
        session.add(checkpoint)
        await session.flush()
    return checkpoint


async def _feed_sequence_for(
    session: AsyncSession, organization_id: UUID, event_id: UUID
) -> int | None:
    """The server sequence already assigned to an event, if its feed row exists."""
    return await session.scalar(
        select(SyncFeedItem.server_sequence)
        .join(SyncEvent, SyncFeedItem.sync_event_id == SyncEvent.id)
        .where(
            SyncEvent.organization_id == organization_id,
            SyncEvent.event_id == event_id,
        )
    )


async def _record_failure(
    session: AsyncSession,
    context: RequestContext,
    *,
    store_id: UUID,
    device_id: UUID,
    envelope: Any,
    error_code: str,
) -> None:
    """Persist why an event did not apply, in its own transaction.

    Called after a rollback has discarded the in-flight row, so this either updates
    a row a committing handler left behind or inserts a fresh one. Without it the
    only trace of a device stuck in a retry loop is in the response it already
    dropped, and ``sync_events.error_code`` would never hold anything.
    """
    existing = await session.scalar(
        select(SyncEvent).where(
            SyncEvent.organization_id == context.organization_id,
            SyncEvent.event_id == envelope.event_id,
        )
    )
    if existing is None:
        session.add(
            SyncEvent(
                organization_id=context.organization_id,
                store_id=store_id,
                device_id=device_id,
                event_id=envelope.event_id,
                event_type=envelope.event_type,
                client_sequence=envelope.client_sequence,
                received_at=utc_now(),
                client_created_at=envelope.created_at,
                applied=False,
                error_code=error_code,
            )
        )
    else:
        existing.applied = False
        existing.error_code = error_code
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race to record the same failure; the other writer's row says the
        # same thing, and losing the note must not fail the whole upload.
        await session.rollback()


async def ingest_events(
    session: AsyncSession, context: RequestContext, device: Device, envelopes: list[Any]
) -> list[SyncAck]:
    """Apply a batch of mutation envelopes exactly once, each independently.

    Every envelope is its own transaction, and that is the whole design rather than
    an implementation detail. Handlers commit their own work -- a sale has to be
    atomic in itself -- so a batch was never one transaction to begin with. Treating
    it as one meant a later envelope's failure rolled back the *bookkeeping* of an
    earlier success whose business effect was already committed: the sale existed,
    but its feed row, its sequence, and the checkpoint advance all vanished, while
    the caller had already been handed a sequence number that no longer described
    anything. Other terminals never saw that sale, and no retry could fix it because
    the duplicate check acknowledged the event as already-applied.

    So each envelope commits before the next is attempted. A failure rejects only
    its own event and is recorded against it; the rest of the batch stands.
    """
    if context.role not in INGEST_ROLES:
        raise Forbidden("Capability denied")
    # ``rollback()`` below expires ORM instances, and AsyncSession cannot lazily
    # refresh on attribute access — so scalar values are captured up front.
    store_id = device.store_id
    device_id = device.id
    acks: list[SyncAck] = []
    for envelope in envelopes:
        acks.append(
            await _ingest_one(
                session, context, device, envelope, store_id=store_id, device_id=device_id
            )
        )
    return acks


async def _ingest_one(
    session: AsyncSession,
    context: RequestContext,
    device: Device,
    envelope: Any,
    *,
    store_id: UUID,
    device_id: UUID,
) -> SyncAck:
    """Apply one envelope and commit its bookkeeping, or reject only it."""
    organization_id = context.organization_id
    event_id = envelope.event_id

    identity_error = _verify_envelope_identity(envelope, context, device)
    if identity_error is not None:
        await _record_failure(
            session,
            context,
            store_id=store_id,
            device_id=device_id,
            envelope=envelope,
            error_code=identity_error,
        )
        return SyncAck(event_id=event_id, duplicate=False, error_code=identity_error)

    existing = await session.scalar(
        select(SyncEvent).where(
            SyncEvent.organization_id == organization_id,
            SyncEvent.event_id == event_id,
        )
    )
    if existing is not None and existing.applied:
        return SyncAck(
            event_id=event_id,
            server_sequence=await _feed_sequence_for(session, organization_id, event_id),
            duplicate=True,
        )

    # An unapplied row is an unfinished attempt, not a duplicate -- a crash between
    # a handler's own commit and the feed write leaves exactly that. Acknowledging it
    # as done would strand the change: applied, but unpullable and unretryable.
    # Handlers are required to be idempotent on the event id, so re-running is safe.
    checkpoint = await _checkpoint(session, organization_id, store_id, device_id)
    if envelope.client_sequence <= checkpoint.last_client_sequence and existing is None:
        return SyncAck(event_id=event_id, duplicate=False, error_code="OUT_OF_ORDER")

    handler = HANDLERS.get(envelope.event_type)
    if handler is None:
        await _record_failure(
            session,
            context,
            store_id=store_id,
            device_id=device_id,
            envelope=envelope,
            error_code="UNSUPPORTED_EVENT_TYPE",
        )
        return SyncAck(event_id=event_id, duplicate=False, error_code="UNSUPPORTED_EVENT_TYPE")

    sync_event = existing
    if sync_event is None:
        sync_event = SyncEvent(
            organization_id=organization_id,
            store_id=store_id,
            device_id=device_id,
            event_id=event_id,
            event_type=envelope.event_type,
            client_sequence=envelope.client_sequence,
            # Server clock: a device weeks offline cannot be trusted to order the feed.
            received_at=utc_now(),
            client_created_at=envelope.created_at,
            applied=False,
        )
        session.add(sync_event)
        try:
            await session.flush()
        except IntegrityError:
            # Another request inserted the same event concurrently; it owns it.
            await session.rollback()
            return SyncAck(
                event_id=event_id,
                server_sequence=await _feed_sequence_for(session, organization_id, event_id),
                duplicate=True,
            )

    try:
        await handler(session, context, device, envelope)
    except DomainError as exc:
        # The handler's own effect is rolled back with it; the client keeps its queue
        # entry and retries once the data is fixed.
        await session.rollback()
        await _record_failure(
            session,
            context,
            store_id=store_id,
            device_id=device_id,
            envelope=envelope,
            error_code=exc.code,
        )
        return SyncAck(event_id=event_id, duplicate=False, error_code=exc.code)

    # A handler that committed internally detached these; re-resolve before writing.
    sync_event = await session.scalar(
        select(SyncEvent).where(
            SyncEvent.organization_id == organization_id, SyncEvent.event_id == event_id
        )
    )
    if sync_event is None:  # pragma: no cover - the handler cannot delete its own event
        raise Conflict("Sync event vanished mid-ingest")
    checkpoint = await _checkpoint(session, organization_id, store_id, device_id)

    server_sequence = await _next_server_sequence(session, store_id)
    sync_event.applied = True
    sync_event.error_code = None
    session.add(
        SyncFeedItem(
            organization_id=organization_id,
            store_id=store_id,
            device_id=device_id,
            sync_event_id=sync_event.id,
            event_type=envelope.event_type,
            payload=envelope.payload,
            received_at=sync_event.received_at,
            server_sequence=server_sequence,
        )
    )
    checkpoint.last_client_sequence = max(
        checkpoint.last_client_sequence, envelope.client_sequence
    )
    # Committed before the next envelope is touched: a later failure must not be able
    # to roll back an effect that is already durable.
    await session.commit()
    return SyncAck(event_id=event_id, server_sequence=server_sequence, duplicate=False)


async def _publish_pending_outbox(
    session: AsyncSession, context: RequestContext, store_id: UUID
) -> None:
    """Project unpublished outbox events for this store into the pull feed.

    This is the sync side of the outbox integration: server-side mutations
    (online sales, voids) get a server sequence here so devices pull them with
    the same gap-free ordering as replayed offline events.
    """
    from app.models import OutboxEvent

    rows = list(
        await session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.organization_id == context.organization_id,
                OutboxEvent.published_at.is_(None),
            )
        )
    )
    published = False
    for row in rows:
        payload_store = row.payload.get("store_id")
        if payload_store is None or str(payload_store) != str(store_id):
            continue
        session.add(
            SyncFeedItem(
                organization_id=context.organization_id,
                store_id=store_id,
                device_id=None,
                sync_event_id=None,
                event_type=row.event_type,
                payload=row.payload,
                received_at=row.created_at,
                server_sequence=await _next_server_sequence(session, store_id),
            )
        )
        row.published_at = utc_now()
        published = True
    if published:
        await session.commit()


async def pull_changes(
    session: AsyncSession,
    context: RequestContext,
    device: Device,
    cursor: int,
    *,
    limit: int = 50,
) -> tuple[list[SyncFeedItem], int, bool]:
    """Feed items after the cursor, ordered by per-store server sequence.

    The returned cursor is also recorded on the device's checkpoint. The client
    still supplies its own cursor -- a device that lost its local state must be able
    to re-read from 0 -- but without a server-side high-water mark there is no way to
    tell a terminal that has fallen days behind from one that is merely idle.
    """
    await _publish_pending_outbox(session, context, device.store_id)
    rows = list(
        await session.scalars(
            select(SyncFeedItem)
            .where(
                SyncFeedItem.organization_id == context.organization_id,
                SyncFeedItem.store_id == device.store_id,
                SyncFeedItem.server_sequence > cursor,
            )
            .order_by(SyncFeedItem.server_sequence)
            .limit(limit + 1)
        )
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].server_sequence if page else cursor
    checkpoint = await _checkpoint(
        session, context.organization_id, device.store_id, device.id
    )
    if next_cursor > checkpoint.last_server_sequence:
        checkpoint.last_server_sequence = next_cursor
        await session.commit()
    return page, next_cursor, has_more
