from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.orders import Order
from app.domains.prescriptions import (
    Prescription,
    PrescriptionFile,
    PrescriptionReview,
    PrescriptionStatus,
)
from app.errors import Conflict, NotFound, ValidationError
from app.models import Role
from app.security import utc_now
from app.services.audit import record_audit

#: Only owners and managers act as the responsible pharmacist. A cashier can
#: upload and attach files but a decision always needs review authority.
REVIEW_ROLES = frozenset({Role.OWNER, Role.MANAGER})

#: A decision is terminal; clarification sends it back for more documents, from
#: where only a decision (never another clarification) may follow.
ALLOWED_REVIEW_TRANSITIONS: dict[PrescriptionStatus, frozenset[PrescriptionStatus]] = {
    PrescriptionStatus.PENDING: frozenset(
        {
            PrescriptionStatus.APPROVED,
            PrescriptionStatus.REJECTED,
            PrescriptionStatus.NEEDS_CLARIFICATION,
        }
    ),
    PrescriptionStatus.NEEDS_CLARIFICATION: frozenset(
        {PrescriptionStatus.APPROVED, PrescriptionStatus.REJECTED}
    ),
}


async def load_prescription(
    session: AsyncSession, context: RequestContext, prescription_id: UUID
) -> Prescription:
    prescription = await session.get(Prescription, prescription_id)
    if prescription is None or prescription.organization_id != context.organization_id:
        raise NotFound("Prescription not found")
    return prescription


async def _load_linked_order(
    session: AsyncSession, context: RequestContext, order_id: UUID
) -> Order:
    """The linked order must exist inside the caller's org and branch."""
    order = await session.get(Order, order_id)
    if (
        order is None
        or order.organization_id != context.organization_id
        or order.store_id != _store_id(context)
    ):
        raise NotFound("Order not found")
    return order


async def create_prescription(
    session: AsyncSession,
    context: RequestContext,
    payload,
    *,
    request_id: str,
) -> Prescription:
    if context.role not in REVIEW_ROLES | {Role.CASHIER}:
        raise ValidationError("Role cannot create prescriptions")
    if payload.order_id is not None:
        await _load_linked_order(session, context, payload.order_id)
    prescription = Prescription(
        organization_id=context.organization_id,
        customer_id=payload.customer_id,
        order_id=payload.order_id,
        status=PrescriptionStatus.PENDING,
        prescriber_name=payload.prescriber_name,
        prescription_number=payload.prescription_number,
        expires_at=payload.expires_at,
        created_at=utc_now(),
    )
    session.add(prescription)
    await session.flush()
    record_audit(
        session,
        context,
        action="prescription.created",
        entity_type="prescription",
        entity_id=prescription.id,
        request_id=request_id,
        after={
            "status": prescription.status.value,
            **({"orderId": str(payload.order_id)} if payload.order_id is not None else {}),
        },
    )
    await session.commit()
    return prescription


async def add_file(
    session: AsyncSession,
    context: RequestContext,
    prescription_id: UUID,
    payload,
    *,
    request_id: str,
) -> PrescriptionFile:
    """Record metadata for an already-uploaded object.

    Only the storage key is stored -- never the binary content -- so PostgreSQL
    stays out of the object-storage business and file bytes are served through
    short-lived scoped URLs outside this service.
    """
    prescription = await load_prescription(session, context, prescription_id)
    if prescription.status in {PrescriptionStatus.APPROVED, PrescriptionStatus.REJECTED}:
        raise Conflict("Files cannot be added to a decided prescription")
    duplicate = await session.scalar(
        select(PrescriptionFile.id).where(
            PrescriptionFile.prescription_id == prescription.id,
            PrescriptionFile.object_key == payload.object_key,
        )
    )
    if duplicate is not None:
        raise Conflict("This file is already attached to the prescription")
    file = PrescriptionFile(
        organization_id=context.organization_id,
        prescription_id=prescription.id,
        object_key=payload.object_key,
        content_type=payload.content_type,
        checksum=payload.checksum,
        uploaded_at=utc_now(),
    )
    session.add(file)
    await session.flush()
    record_audit(
        session,
        context,
        action="prescription.file_added",
        entity_type="prescription_file",
        entity_id=file.id,
        request_id=request_id,
        after={"prescription_id": str(prescription.id), "checksum": payload.checksum},
    )
    await session.commit()
    return file


async def review_prescription(
    session: AsyncSession,
    context: RequestContext,
    prescription_id: UUID,
    payload,
    *,
    request_id: str,
) -> tuple[Prescription, PrescriptionReview]:
    """Record one pharmacist decision and move the prescription's status.

    The review row is append-only history; the prescription's status mirrors the
    latest decision so order gating has a single field to read.
    """
    if context.role not in REVIEW_ROLES:
        from app.errors import Forbidden

        raise Forbidden("Only owners and managers review prescriptions")
    target = PrescriptionStatus(payload.status)
    prescription = await load_prescription(session, context, prescription_id)
    current = PrescriptionStatus(prescription.status)
    allowed = ALLOWED_REVIEW_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise Conflict(
            f"Cannot review a {current.value} prescription to {target.value}"
        )

    now = utc_now()
    review = PrescriptionReview(
        organization_id=context.organization_id,
        store_id=_store_id(context),
        prescription_id=prescription.id,
        status=target,
        pharmacist_user_id=context.user_id,
        notes=payload.notes,
        reviewed_at=now,
    )
    session.add(review)
    prescription.status = target
    await session.flush()
    record_audit(
        session,
        context,
        action="prescription.reviewed",
        entity_type="prescription",
        entity_id=prescription.id,
        request_id=request_id,
        before={"status": current.value},
        after={"status": target.value},
    )
    await session.commit()
    return prescription, review


async def list_prescriptions(
    session: AsyncSession,
    context: RequestContext,
    *,
    status: PrescriptionStatus | None = None,
    customer_id: UUID | None = None,
    order_id: UUID | None = None,
) -> list[Prescription]:
    scope = [Prescription.organization_id == context.organization_id]
    if status is not None:
        scope.append(Prescription.status == status)
    if customer_id is not None:
        scope.append(Prescription.customer_id == customer_id)
    if order_id is not None:
        scope.append(Prescription.order_id == order_id)
    return list(
        await session.scalars(
            select(Prescription)
            .where(*scope)
            .order_by(Prescription.created_at.desc(), Prescription.id)
        )
    )


async def attach_to_order(
    session: AsyncSession, context: RequestContext, prescription: Prescription, order_id: UUID, *,
    request_id: str,
) -> Prescription:
    """Link a prescription to the order it authorizes.

    This is the only path that makes the order-side prescription gate reachable:
    an order that requires a prescription cannot be accepted until one linked to
    it is approved.
    """
    order = await _load_linked_order(session, context, order_id)
    prescription.order_id = order.id
    await session.flush()
    record_audit(
        session,
        context,
        action="prescription.attached_to_order",
        entity_type="prescription",
        entity_id=prescription.id,
        request_id=request_id,
        after={"orderId": str(order.id)},
    )
    await session.commit()
    return prescription


async def order_prescription_approved(
    session: AsyncSession, order_id: UUID
) -> bool:
    """Whether at least one approved prescription is linked to the order."""
    row = await session.scalar(
        select(Prescription.id).where(
            Prescription.order_id == order_id,
            Prescription.status == PrescriptionStatus.APPROVED,
        )
    )
    return row is not None


async def load_files(session: AsyncSession, prescription_ids: list[UUID]) -> dict[UUID, list[PrescriptionFile]]:
    if not prescription_ids:
        return {}
    rows = await session.scalars(
        select(PrescriptionFile).where(PrescriptionFile.prescription_id.in_(prescription_ids))
    )
    grouped: dict[UUID, list[PrescriptionFile]] = {}
    for file in rows:
        grouped.setdefault(file.prescription_id, []).append(file)
    return grouped


def _store_id(context: RequestContext) -> UUID:
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    return context.store_id
