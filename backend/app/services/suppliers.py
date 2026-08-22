from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.products import PharmacyProduct
from app.domains.suppliers import Supplier, SupplierLedgerEntry, SupplierProduct, SupplierStatus
from app.errors import Conflict, NotFound, ValidationError
from app.models import Role
from app.schemas.suppliers import (
    LedgerEntryCreateRequest,
    SupplierCreateRequest,
    SupplierProductCreateRequest,
    SupplierStatusUpdateRequest,
    SupplierUpdateRequest,
)
from app.security import utc_now
from app.services.audit import record_audit, redact

#: Roles that may move supplier money. Adjustments additionally need OWNER/MANAGER
#: (enforced at the router), payments may be recorded by managers but never by
#: cashiers or inventory staff.
LEDGER_ROLES = frozenset({Role.OWNER, Role.MANAGER})


async def load_supplier(
    session: AsyncSession, context: RequestContext, supplier_id: UUID
) -> Supplier:
    """Fetch a supplier under the caller's organization.

    A supplier owned by another organization is reported as missing so ids
    cannot be probed across tenants.
    """
    supplier = await session.get(Supplier, supplier_id)
    if supplier is None or supplier.organization_id != context.organization_id:
        raise NotFound("Supplier not found")
    return supplier


async def list_suppliers(
    session: AsyncSession, context: RequestContext, *, include_inactive: bool = True
) -> list[Supplier]:
    query = select(Supplier).where(Supplier.organization_id == context.organization_id)
    if not include_inactive:
        query = query.where(Supplier.status == SupplierStatus.ACTIVE)
    return list(await session.scalars(query.order_by(Supplier.name)))


async def create_supplier(
    session: AsyncSession,
    context: RequestContext,
    payload: SupplierCreateRequest,
    *,
    request_id: str,
) -> Supplier:
    name = payload.name.strip()
    duplicate = await session.scalar(
        select(Supplier.id).where(
            Supplier.organization_id == context.organization_id, Supplier.name == name
        )
    )
    if duplicate is not None:
        raise Conflict(f"Supplier '{name}' already exists in this organization")

    supplier = Supplier(
        organization_id=context.organization_id,
        name=name,
        phone=payload.phone,
        address=payload.address,
        status=SupplierStatus.ACTIVE,
    )
    session.add(supplier)
    try:
        await session.flush()
        record_audit(
            session,
            context,
            action="supplier.created",
            entity_type="supplier",
            entity_id=supplier.id,
            request_id=request_id,
            after=redact({"name": supplier.name, "phone": supplier.phone, "status": supplier.status.value}),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(f"Supplier '{name}' already exists in this organization") from exc
    except Exception:
        await session.rollback()
        raise
    return supplier


async def update_supplier(
    session: AsyncSession,
    context: RequestContext,
    supplier_id: UUID,
    payload: SupplierUpdateRequest,
    *,
    request_id: str,
) -> Supplier:
    supplier = await load_supplier(session, context, supplier_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return supplier
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        duplicate = await session.scalar(
            select(Supplier.id).where(
                Supplier.organization_id == context.organization_id,
                Supplier.name == changes["name"],
                Supplier.id != supplier.id,
            )
        )
        if duplicate is not None:
            raise Conflict(f"Supplier '{changes['name']}' already exists in this organization")

    before = {"name": supplier.name, "phone": supplier.phone, "address": supplier.address}
    for field, value in changes.items():
        setattr(supplier, field, value)
    record_audit(
        session,
        context,
        action="supplier.updated",
        entity_type="supplier",
        entity_id=supplier.id,
        request_id=request_id,
        before=redact(before),
        after=redact({"name": supplier.name, "phone": supplier.phone, "address": supplier.address}),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Supplier name already exists in this organization") from exc
    except Exception:
        await session.rollback()
        raise
    return supplier


async def update_supplier_status(
    session: AsyncSession,
    context: RequestContext,
    supplier_id: UUID,
    payload: SupplierStatusUpdateRequest,
    *,
    request_id: str,
) -> Supplier:
    """Deactivate (or reactivate) a supplier. Ledger history is never touched."""
    supplier = await load_supplier(session, context, supplier_id)
    target = SupplierStatus(payload.status)
    if supplier.status is target:
        return supplier
    before = supplier.status
    supplier.status = target
    record_audit(
        session,
        context,
        action="supplier.status_changed",
        entity_type="supplier",
        entity_id=supplier.id,
        request_id=request_id,
        before=redact({"status": before.value}),
        after=redact({"status": target.value}),
    )
    await session.commit()
    return supplier


# --- product mappings -------------------------------------------------------


async def _load_org_product(
    session: AsyncSession, context: RequestContext, pharmacy_product_id: UUID
) -> PharmacyProduct:
    product = await session.get(PharmacyProduct, pharmacy_product_id)
    if product is None or product.organization_id != context.organization_id:
        raise NotFound("Product not found")
    return product


async def list_supplier_products(
    session: AsyncSession, context: RequestContext, supplier_id: UUID
) -> list[SupplierProduct]:
    await load_supplier(session, context, supplier_id)
    return list(
        await session.scalars(
            select(SupplierProduct)
            .where(SupplierProduct.supplier_id == supplier_id)
            .order_by(SupplierProduct.id)
        )
    )


async def create_supplier_product(
    session: AsyncSession,
    context: RequestContext,
    supplier_id: UUID,
    payload: SupplierProductCreateRequest,
    *,
    request_id: str,
) -> SupplierProduct:
    await load_supplier(session, context, supplier_id)
    await _load_org_product(session, context, payload.pharmacy_product_id)
    existing = await session.scalar(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier_id,
            SupplierProduct.pharmacy_product_id == payload.pharmacy_product_id,
        )
    )
    if existing is not None:
        raise Conflict("This product is already mapped to the supplier")

    if payload.preferred:
        await clear_preferred(session, context.organization_id, payload.pharmacy_product_id)

    mapping = SupplierProduct(
        supplier_id=supplier_id,
        pharmacy_product_id=payload.pharmacy_product_id,
        supplier_sku=payload.supplier_sku,
        preferred=payload.preferred,
    )
    session.add(mapping)
    try:
        await session.flush()
        record_audit(
            session,
            context,
            action="supplier.product_linked",
            entity_type="supplier_product",
            entity_id=mapping.id,
            request_id=request_id,
            after=redact(
                {
                    "supplier_id": str(supplier_id),
                    "pharmacy_product_id": str(payload.pharmacy_product_id),
                    "preferred": payload.preferred,
                }
            ),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("This product is already mapped to the supplier") from exc
    except Exception:
        await session.rollback()
        raise
    return mapping


async def clear_preferred(
    session: AsyncSession, organization_id: UUID, pharmacy_product_id: UUID
) -> None:
    """Preferred is exclusive per product within the organization."""
    rows = await session.scalars(
        select(SupplierProduct)
        .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
        .where(
            Supplier.organization_id == organization_id,
            SupplierProduct.pharmacy_product_id == pharmacy_product_id,
            SupplierProduct.preferred.is_(True),
        )
    )
    for row in rows:
        row.preferred = False


# --- ledger -----------------------------------------------------------------


async def append_ledger_entry(
    session: AsyncSession,
    context: RequestContext,
    supplier_id: UUID,
    entry_type: str,
    amount: Decimal,
    *,
    reference_type: str | None = None,
    reference_id: UUID | None = None,
    idempotency_key: str,
    note: str | None = None,
    commit: bool = True,
    request_id: str = "unknown",
) -> SupplierLedgerEntry:
    """Append an immutable ledger row; idempotent per (organization, key).

    Positive amounts increase what we owe the supplier (purchases); negative
    amounts reduce it (payments, returns). A repeated idempotency key returns
    the original entry without creating a duplicate.
    """
    supplier = await load_supplier(session, context, supplier_id)
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    if not isinstance(amount, Decimal) or amount == 0:
        raise ValidationError("Ledger amount must be a non-zero decimal")

    existing = await session.scalar(
        select(SupplierLedgerEntry).where(
            SupplierLedgerEntry.organization_id == context.organization_id,
            SupplierLedgerEntry.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.supplier_id != supplier_id:
            raise Conflict("Idempotency key already used for a different supplier")
        return existing

    entry = SupplierLedgerEntry(
        organization_id=context.organization_id,
        store_id=context.store_id,
        supplier_id=supplier.id,
        entry_type=entry_type,
        amount=amount,
        reference_type=reference_type,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
        note=note,
        created_at=utc_now(),
    )
    session.add(entry)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Idempotency key already used") from exc
    record_audit(
        session,
        context,
        action=f"supplier.ledger_{entry_type}",
        entity_type="supplier_ledger_entry",
        entity_id=entry.id,
        request_id=request_id,
        after=redact(
            {
                "supplier_id": str(supplier_id),
                "entry_type": entry_type,
                "amount": str(amount),
            }
        ),
    )
    if commit:
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise Conflict("Idempotency key already used") from exc
        except Exception:
            await session.rollback()
            raise
    return entry


async def record_supplier_payment(
    session: AsyncSession,
    context: RequestContext,
    supplier_id: UUID,
    payload: LedgerEntryCreateRequest,
    *,
    idempotency_key: str,
    request_id: str,
) -> SupplierLedgerEntry:
    """A payment reduces the payable balance, so the stored amount is negative."""
    return await append_ledger_entry(
        session,
        context,
        supplier_id,
        "payment",
        -abs(payload.amount),
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        idempotency_key=idempotency_key,
        note=payload.note,
        request_id=request_id,
    )


async def record_supplier_adjustment(
    session: AsyncSession,
    context: RequestContext,
    supplier_id: UUID,
    payload: LedgerEntryCreateRequest,
    *,
    idempotency_key: str,
    request_id: str,
) -> SupplierLedgerEntry:
    """An adjustment books the signed amount exactly as submitted."""
    return await append_ledger_entry(
        session,
        context,
        supplier_id,
        "adjustment",
        payload.amount,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        idempotency_key=idempotency_key,
        note=payload.note,
        request_id=request_id,
    )


async def list_ledger(
    session: AsyncSession,
    context: RequestContext,
    supplier_id: UUID,
    *,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[SupplierLedgerEntry], int]:
    await load_supplier(session, context, supplier_id)
    scope = (
        SupplierLedgerEntry.organization_id == context.organization_id,
        SupplierLedgerEntry.supplier_id == supplier_id,
    )
    total = await session.scalar(select(func.count()).select_from(SupplierLedgerEntry).where(*scope))
    items = list(
        await session.scalars(
            select(SupplierLedgerEntry)
            .where(*scope)
            .order_by(SupplierLedgerEntry.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, int(total or 0)


async def supplier_balance(
    session: AsyncSession, supplier_id: UUID, *, organization_id: UUID | None = None
) -> Decimal:
    """Sum of signed amounts: positive means we owe the supplier."""
    scope: tuple[Any, ...] = (SupplierLedgerEntry.supplier_id == supplier_id,)
    if organization_id is not None:
        scope = (*scope, SupplierLedgerEntry.organization_id == organization_id)
    balance = await session.scalar(
        select(func.coalesce(func.sum(SupplierLedgerEntry.amount), 0)).where(*scope)
    )
    return Decimal(balance or 0)
