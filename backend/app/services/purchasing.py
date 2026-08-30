from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.catalog import CatalogBarcode, CatalogProduct
from app.domains.inventory import InventoryMovementType
from app.domains.products import PharmacyProduct
from app.domains.purchasing import Purchase, PurchaseItem, PurchaseStatus
from app.domains.suppliers import SupplierLedgerEntry, SupplierStatus
from app.domains.sync import StoreSequence
from app.errors import Conflict, NotFound, ValidationError
from app.models import Role, StoreProduct
from app.schemas.purchasing import (
    PurchaseCreateRequest,
    PurchaseReceiptLine,
    PurchaseReceiptPayment,
    PurchaseReceiptResponse,
    PurchaseReceiveItem,
    PurchaseReceiveRequest,
    PurchaseReturnRequest,
)
from app.security import utc_now
from app.services.audit import enqueue_outbox, record_audit, redact
from app.services.cash import current_session
from app.services.inventory import (
    InsufficientStock,
    _intake_sku,
    allocate_fefo_for_product,
    consume_allocations,
    load_store_product,
    normalize_rack,
)
from app.services.payments import allowed_payment_methods
from app.services.stores import load_store
from app.services.suppliers import append_ledger_entry, load_supplier, supplier_balance

WRITER_ROLES = frozenset({Role.OWNER, Role.MANAGER})
COST_ROLES = frozenset({Role.OWNER, Role.MANAGER})
RECEIVER_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.CASHIER, Role.INVENTORY_STAFF})
CENT = Decimal("0.01")

RETURN_OF_PREFIX = "return-of:"


def can_see_costs(context: RequestContext) -> bool:
    return context.role in COST_ROLES


async def load_purchase(
    session: AsyncSession, context: RequestContext, purchase_id: UUID
) -> Purchase:
    """A purchase of another tenant (or branch) does not exist for the caller."""
    purchase = await session.get(Purchase, purchase_id)
    if purchase is None or purchase.organization_id != context.organization_id:
        raise NotFound("Purchase not found")
    if context.store_id is not None and purchase.store_id != context.store_id:
        raise NotFound("Purchase not found")
    return purchase


async def _load_items(session: AsyncSession, purchase_id: UUID) -> list[PurchaseItem]:
    return list(
        await session.scalars(
            select(PurchaseItem)
            .where(PurchaseItem.purchase_id == purchase_id)
            .order_by(PurchaseItem.id)
        )
    )


async def create_purchase(
    session: AsyncSession,
    context: RequestContext,
    payload: PurchaseCreateRequest,
    *,
    request_id: str,
    commit: bool = True,
) -> Purchase:
    """Create a DRAFT with server-computed totals; nothing touches stock yet."""
    if context.role not in WRITER_ROLES:
        raise Conflict("Only owners and managers may create purchases")
    if context.store_id is None:
        raise NotFound("Purchase not found")
    await load_supplier(session, context, payload.supplier_id)
    for item in payload.items:
        await load_store_product(session, context, item.store_product_id)

    total = sum((item.quantity * item.unit_cost for item in payload.items), Decimal(0))
    purchase = Purchase(
        organization_id=context.organization_id,
        store_id=context.store_id,
        supplier_id=payload.supplier_id,
        status=PurchaseStatus.DRAFT,
        invoice_number=payload.invoice_number,
        note=payload.note,
        purchased_at=payload.purchased_at or utc_now().date(),
        total_amount=total.quantize(Decimal("0.01")),
        idempotency_key=f"draft:{utc_now().isoformat()}",
    )
    session.add(purchase)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Purchase could not be created") from exc
    for item in payload.items:
        session.add(
            PurchaseItem(
                purchase_id=purchase.id,
                store_product_id=item.store_product_id,
                quantity=item.quantity,
                unit_cost=item.unit_cost,
                line_total=(item.quantity * item.unit_cost).quantize(CENT),
                batch_number=item.batch_number.strip(),
                expiry_date=item.expiry_date,
            )
        )
    record_audit(
        session,
        context,
        action="purchase.created",
        entity_type="purchase",
        entity_id=purchase.id,
        request_id=request_id,
        after=redact({"supplier_id": str(payload.supplier_id), "total": str(total)}),
    )
    if commit:
        await session.commit()
    return purchase


async def _next_grn(session: AsyncSession, store_id: UUID) -> str:
    sequence = await session.get(StoreSequence, store_id, with_for_update=True)
    if sequence is None:
        sequence = StoreSequence(
            store_id=store_id,
            last_sequence=0,
            last_receipt_sequence=0,
            last_grn_sequence=0,
        )
        session.add(sequence)
        await session.flush()
    sequence.last_grn_sequence += 1
    await session.flush()
    return f"GRN-{sequence.last_grn_sequence:08d}"


async def _resolve_receiving_shelf(
    session: AsyncSession, context: RequestContext, item: PurchaseReceiveItem
) -> tuple[StoreProduct, PharmacyProduct]:
    assert context.store_id is not None
    store = await load_store(session, context, context.store_id)
    product: PharmacyProduct | None = None
    shelf: StoreProduct | None = None

    if item.store_product_id is not None:
        shelf = await load_store_product(session, context, item.store_product_id)
        product = await session.get(PharmacyProduct, shelf.pharmacy_product_id)
    elif item.pharmacy_product_id is not None:
        product = await session.get(PharmacyProduct, item.pharmacy_product_id)
        if product is None or product.organization_id != context.organization_id:
            raise NotFound("Product not found")
    elif item.catalog_product_id is not None:
        catalog = await session.get(CatalogProduct, item.catalog_product_id)
        if catalog is None or not catalog.active:
            raise NotFound("Catalog product not found")
        product = await session.scalar(
            select(PharmacyProduct).where(
                PharmacyProduct.organization_id == context.organization_id,
                PharmacyProduct.catalog_product_id == catalog.id,
            )
        )
        if product is None:
            barcode = await session.scalar(
                select(CatalogBarcode.barcode)
                .where(CatalogBarcode.catalog_product_id == catalog.id)
                .order_by(CatalogBarcode.barcode)
            )
            if barcode is not None and await session.scalar(
                select(PharmacyProduct.id).where(
                    PharmacyProduct.organization_id == context.organization_id,
                    PharmacyProduct.barcode == barcode,
                    PharmacyProduct.active.is_(True),
                )
            ) is not None:
                barcode = None
            product = PharmacyProduct(
                organization_id=context.organization_id,
                catalog_product_id=catalog.id,
                name=catalog.name,
                barcode=barcode,
                unit=catalog.package_unit,
                active=True,
            )
            session.add(product)
            await session.flush()
        else:
            product.active = True
    else:
        assert item.custom_product is not None
        custom = item.custom_product
        if custom.barcode is not None and await session.scalar(
            select(PharmacyProduct.id).where(
                PharmacyProduct.organization_id == context.organization_id,
                PharmacyProduct.barcode == custom.barcode,
                PharmacyProduct.active.is_(True),
            )
        ) is not None:
            raise Conflict(f"Barcode '{custom.barcode}' already exists in this organization")
        product = PharmacyProduct(
            organization_id=context.organization_id,
            name=custom.name.strip(),
            unit=custom.unit.strip(),
            barcode=custom.barcode,
            active=True,
        )
        session.add(product)
        await session.flush()

    if product is None:
        raise NotFound("Product not found")
    if shelf is None:
        shelf = await session.scalar(
            select(StoreProduct).where(
                StoreProduct.store_id == store.id,
                StoreProduct.pharmacy_product_id == product.id,
            )
        )
    if shelf is None:
        if item.shelf.sale_price is None:
            raise ValidationError("New shelf items require salePrice")
        shelf = StoreProduct(
            organization_id=context.organization_id,
            store_id=store.id,
            pharmacy_product_id=product.id,
            sku=item.shelf.sku or await _intake_sku(session, store.id, product.name),
            sale_price=item.shelf.sale_price,
            minimum_stock=item.shelf.minimum_stock or Decimal(0),
            rack=normalize_rack(item.shelf.rack),
            active=True,
        )
        session.add(shelf)
        await session.flush()
    else:
        shelf.active = True

    requested_barcode = item.shelf.barcode
    if requested_barcode is not None:
        duplicate = await session.scalar(
            select(PharmacyProduct.id).where(
                PharmacyProduct.organization_id == context.organization_id,
                PharmacyProduct.barcode == requested_barcode,
                PharmacyProduct.id != product.id,
                PharmacyProduct.active.is_(True),
            )
        )
        if duplicate is not None:
            raise Conflict(f"Barcode '{requested_barcode}' already exists in this organization")
        product.barcode = requested_barcode
    product.active = True
    return shelf, product


async def receive_purchase(
    session: AsyncSession,
    context: RequestContext,
    payload: PurchaseReceiveRequest,
    *,
    idempotency_key: str,
    request_id: str,
) -> PurchaseReceiptResponse:
    """Create and confirm a supplier receipt, including tenders, in one transaction."""
    if context.role not in RECEIVER_ROLES or context.store_id is None:
        raise ValidationError("Store receiving access required")
    replay = await session.scalar(
        select(Purchase).where(
            Purchase.organization_id == context.organization_id,
            Purchase.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if replay.status is not PurchaseStatus.CONFIRMED:
            raise Conflict("Idempotency key already used for an unfinished purchase")
        return await purchase_receipt(session, context, replay.id)

    supplier = await load_supplier(session, context, payload.supplier_id)
    if supplier.status is not SupplierStatus.ACTIVE:
        raise Conflict("Inactive suppliers cannot receive new purchases")

    configured = await allowed_payment_methods(session, context)
    seen: set[str] = set()
    for payment in payload.payments:
        if payment.method == "due" or payment.method not in configured:
            raise ValidationError(f"Payment method '{payment.method}' is not active")
        if payment.method in seen:
            raise ValidationError("Each payment method may appear only once")
        seen.add(payment.method)
    if len(seen - {"cash"}) > 1:
        raise ValidationError("Choose at most one digital payment method")
    if "cash" in seen and await current_session(session, context) is None:
        raise Conflict("Open a cash shift before paying a supplier in cash")

    receipt_number = await _next_grn(session, context.store_id)
    resolved: list[tuple[PurchaseReceiveItem, StoreProduct, PharmacyProduct, Decimal, Decimal]] = []
    entered_total = Decimal(0)
    for item in payload.items:
        shelf, product = await _resolve_receiving_shelf(session, context, item)
        line_total = (
            Decimal(item.line_total)
            if item.line_total is not None
            else (Decimal(item.quantity) * Decimal(item.unit_cost)).quantize(CENT)
            if item.unit_cost is not None
            else Decimal(0).quantize(CENT)
        )
        unit_cost = (
            Decimal(item.unit_cost)
            if item.unit_cost is not None
            else (line_total / Decimal(item.quantity)).quantize(CENT)
            if item.line_total is not None
            else Decimal(0).quantize(CENT)
        )
        entered_total += line_total
        resolved.append((item, shelf, product, unit_cost, line_total))
    total = (
        Decimal(payload.total_amount)
        if payload.total_amount is not None
        else entered_total
    ).quantize(CENT)
    paid = sum((Decimal(payment.amount) for payment in payload.payments), Decimal(0)).quantize(CENT)
    if paid > total:
        raise ValidationError("Supplier payments cannot exceed the receipt total")

    purchase = Purchase(
        organization_id=context.organization_id,
        store_id=context.store_id,
        supplier_id=supplier.id,
        status=PurchaseStatus.CONFIRMED,
        invoice_number=payload.invoice_number,
        receipt_number=receipt_number,
        total_amount=total,
        purchased_at=payload.purchased_at or utc_now().date(),
        idempotency_key=idempotency_key,
        note=payload.note,
        confirmed_at=utc_now(),
    )
    session.add(purchase)
    await session.flush()

    purchase_items: list[PurchaseItem] = []
    for index, (item, shelf, _product, unit_cost, line_total) in enumerate(resolved, start=1):
        purchase_item = PurchaseItem(
            purchase_id=purchase.id,
            store_product_id=shelf.id,
            quantity=item.quantity,
            unit_cost=unit_cost,
            line_total=line_total,
            batch_number=(item.batch_number or f"{receipt_number}-{index}")[:100],
            expiry_date=item.expiry_date,
        )
        session.add(purchase_item)
        purchase_items.append(purchase_item)
    await session.flush()

    for purchase_item in purchase_items:
        await receive_for_item(
            session,
            context,
            purchase,
            purchase_item,
            idempotency_key=f"receive:{purchase.id}",
            request_id=request_id,
        )
    await append_ledger_entry(
        session,
        context,
        supplier.id,
        "purchase",
        total,
        reference_type="purchase",
        reference_id=purchase.id,
        idempotency_key=f"purchase:{purchase.id}",
        commit=False,
        request_id=request_id,
    )
    for index, payment in enumerate(payload.payments):
        await append_ledger_entry(
            session,
            context,
            supplier.id,
            "payment",
            -Decimal(payment.amount),
            reference_type="purchase",
            reference_id=purchase.id,
            idempotency_key=f"purchase-payment:{purchase.id}:{index}",
            payment_method=payment.method,
            provider_reference=payment.provider_reference,
            commit=False,
            request_id=request_id,
        )
    record_audit(
        session,
        context,
        action="purchase.received",
        entity_type="purchase",
        entity_id=purchase.id,
        request_id=request_id,
        after=redact({"receipt_number": receipt_number, "supplier_id": str(supplier.id), "total": str(total), "paid": str(paid)}),
    )
    enqueue_outbox(
        session,
        organization_id=context.organization_id,
        event_type="purchase.confirmed",
        aggregate_type="purchase",
        aggregate_id=purchase.id,
        payload={"purchase_id": str(purchase.id), "store_id": str(context.store_id), "supplier_id": str(supplier.id), "receipt_number": receipt_number, "total_amount": str(total), "item_count": len(purchase_items)},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Supplier receipt conflicts with an existing product or receipt") from exc
    except Exception:
        await session.rollback()
        raise
    return await purchase_receipt(session, context, purchase.id)


async def purchase_receipt(
    session: AsyncSession, context: RequestContext, purchase_id: UUID
) -> PurchaseReceiptResponse:
    purchase = await load_purchase(session, context, purchase_id)
    if purchase.status is not PurchaseStatus.CONFIRMED or purchase.receipt_number is None or purchase.confirmed_at is None:
        raise Conflict("Only confirmed purchases have a goods-received voucher")
    supplier = await load_supplier(session, context, purchase.supplier_id)
    item_rows = list(
        await session.execute(
            select(PurchaseItem, StoreProduct, PharmacyProduct)
            .join(StoreProduct, StoreProduct.id == PurchaseItem.store_product_id)
            .join(PharmacyProduct, PharmacyProduct.id == StoreProduct.pharmacy_product_id)
            .where(PurchaseItem.purchase_id == purchase.id)
            .order_by(PurchaseItem.id)
        )
    )
    ledger = list(
        await session.scalars(
            select(SupplierLedgerEntry)
            .where(
                SupplierLedgerEntry.reference_type == "purchase",
                SupplierLedgerEntry.reference_id == purchase.id,
                SupplierLedgerEntry.entry_type == "payment",
            )
            .order_by(SupplierLedgerEntry.created_at, SupplierLedgerEntry.id)
        )
    )
    payments = [
        PurchaseReceiptPayment(
            method=entry.payment_method or "payment",
            amount=abs(Decimal(entry.amount)),
            provider_reference=entry.provider_reference,
        )
        for entry in ledger
    ]
    paid = sum((payment.amount for payment in payments), Decimal(0)).quantize(CENT)
    return PurchaseReceiptResponse(
        purchase_id=purchase.id,
        receipt_number=purchase.receipt_number,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        invoice_number=purchase.invoice_number,
        purchased_at=purchase.purchased_at,
        confirmed_at=purchase.confirmed_at,
        total_amount=Decimal(purchase.total_amount),
        paid_amount=paid,
        credit_amount=(Decimal(purchase.total_amount) - paid).quantize(CENT),
        supplier_balance_after=(await supplier_balance(session, supplier.id, organization_id=context.organization_id)).quantize(CENT),
        lines=[
            PurchaseReceiptLine(
                purchase_item_id=item.id,
                store_product_id=shelf.id,
                name=product.name,
                sku=shelf.sku,
                unit=product.unit,
                quantity=Decimal(item.quantity),
                unit_cost=Decimal(item.unit_cost),
                line_total=Decimal(item.line_total),
                batch_number=item.batch_number,
                expiry_date=item.expiry_date,
            )
            for item, shelf, product in item_rows
        ],
        payments=payments,
    )


async def confirm_purchase(
    session: AsyncSession,
    context: RequestContext,
    purchase_id: UUID,
    *,
    idempotency_key: str,
    request_id: str,
) -> Purchase:
    """Confirm a draft in one atomic transaction.

    Receives every item as an inventory batch, books the supplier liability,
    audits, and stages an outbox event. Any failure rolls the whole thing back.
    """
    if context.role not in WRITER_ROLES:
        raise Conflict("Only owners and managers may confirm purchases")
    purchase = await load_purchase(session, context, purchase_id)

    replay = purchase.status is PurchaseStatus.CONFIRMED and purchase.idempotency_key == idempotency_key
    if not replay:
        clash = await session.scalar(
            select(Purchase.id).where(
                Purchase.organization_id == context.organization_id,
                Purchase.idempotency_key == idempotency_key,
                Purchase.id != purchase.id,
            )
        )
        if clash is not None or purchase.status is PurchaseStatus.CONFIRMED:
            raise Conflict("Purchase already confirmed or idempotency key already used")

    items = await _load_items(session, purchase.id)
    now = utc_now()
    purchase.status = PurchaseStatus.CONFIRMED
    purchase.confirmed_at = now
    purchase.idempotency_key = idempotency_key
    if purchase.receipt_number is None:
        purchase.receipt_number = await _next_grn(session, purchase.store_id)

    for item in items:
        await receive_for_item(
            session, context, purchase, item, idempotency_key=idempotency_key, request_id=request_id
        )

    await append_ledger_entry(
        session,
        context,
        purchase.supplier_id,
        "purchase",
        Decimal(purchase.total_amount),
        reference_type="purchase",
        reference_id=purchase.id,
        idempotency_key=f"purchase-confirm:{idempotency_key}",
        commit=False,
        request_id=request_id,
    )
    record_audit(
        session,
        context,
        action="purchase.confirmed",
        entity_type="purchase",
        entity_id=purchase.id,
        request_id=request_id,
        after=redact({"supplier_id": str(purchase.supplier_id), "total": str(purchase.total_amount)}),
    )
    enqueue_outbox(
        session,
        organization_id=context.organization_id,
        event_type="purchase.confirmed",
        aggregate_type="purchase",
        aggregate_id=purchase.id,
        payload={
            "purchase_id": str(purchase.id),
            "store_id": str(purchase.store_id),
            "supplier_id": str(purchase.supplier_id),
            "total_amount": str(purchase.total_amount),
            "item_count": len(items),
        },
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Purchase already confirmed") from exc
    except Exception:
        await session.rollback()
        raise
    return purchase


async def receive_for_item(
    session: AsyncSession,
    context: RequestContext,
    purchase: Purchase,
    item: PurchaseItem,
    *,
    idempotency_key: str,
    request_id: str,
) -> None:
    from app.services.inventory import receive_batch

    await receive_batch(
        session,
        context,
        item.store_product_id,
        batch_number=item.batch_number,
        expiry_date=item.expiry_date,
        unit_cost=Decimal(item.unit_cost),
        quantity=Decimal(item.quantity),
        reference_type="purchase",
        reference_id=purchase.id,
        idempotency_key=f"{idempotency_key}:{item.id}",
        commit=False,
        request_id=request_id,
    )


async def _returned_quantities(
    session: AsyncSession, original: Purchase
) -> dict[UUID, Decimal]:
    """Aggregate quantities already returned per original purchase item."""
    prior = await session.scalars(
        select(Purchase).where(
            Purchase.organization_id == original.organization_id,
            Purchase.store_id == original.store_id,
            Purchase.status == PurchaseStatus.RETURNED,
            Purchase.note == f"{RETURN_OF_PREFIX}{original.id}",
        )
    )
    returned: dict[UUID, Decimal] = {}
    for ret in prior:
        for item in await _load_items(session, ret.id):
            if not item.batch_number.startswith("RETURN:"):
                continue
            try:
                original_item_id = UUID(item.batch_number.removeprefix("RETURN:"))
            except ValueError:
                continue
            returned[original_item_id] = returned.get(original_item_id, Decimal(0)) + Decimal(item.quantity)
    return returned


async def return_purchase(
    session: AsyncSession,
    context: RequestContext,
    purchase_id: UUID,
    payload: PurchaseReturnRequest,
    *,
    request_id: str,
) -> Purchase:
    """Return lines of a confirmed purchase back to the supplier.

    Reduces stock FEFO (never below zero), books a negative supplier ledger
    entry, and records the return as its own RETURNED purchase.
    """
    if context.role not in WRITER_ROLES:
        raise Conflict("Only owners and managers may create returns")
    original = await load_purchase(session, context, purchase_id)
    if original.status is not PurchaseStatus.CONFIRMED:
        raise Conflict("Only confirmed purchases can be returned")

    items = {item.id: item for item in await _load_items(session, original.id)}
    returned = await _returned_quantities(session, original)

    by_product: dict[UUID, Decimal] = {}
    returned_lines: list[tuple[PurchaseItem, Decimal, Decimal]] = []
    in_request_by_item: dict[UUID, Decimal] = {}
    total = Decimal(0)
    for line in payload.lines:
        item = items.get(line.purchase_item_id)
        if item is None:
            raise NotFound("Purchase item not found")
        already = returned.get(item.id, Decimal(0)) + in_request_by_item.get(item.id, Decimal(0))
        if Decimal(item.quantity) - already < Decimal(line.quantity):
            raise Conflict("Return quantity exceeds remaining purchasable quantity")
        by_product[item.store_product_id] = by_product.get(item.store_product_id, Decimal(0)) + Decimal(line.quantity)
        in_request_by_item[item.id] = in_request_by_item.get(item.id, Decimal(0)) + Decimal(line.quantity)
        returned_value = (
            Decimal(item.line_total) * Decimal(line.quantity) / Decimal(item.quantity)
        ).quantize(CENT)
        returned_lines.append((item, Decimal(line.quantity), returned_value))
        total += returned_value

    allocations_by_product: dict[UUID, list[Any]] = {}
    for store_product_id, quantity in by_product.items():
        result = await allocate_fefo_for_product(session, context, store_product_id, quantity)
        if not result.ok:
            raise Conflict("Insufficient stock to process the return")
        allocations_by_product[store_product_id] = result.allocations

    return_purchase_row = Purchase(
        organization_id=context.organization_id,
        store_id=context.store_id,
        supplier_id=original.supplier_id,
        status=PurchaseStatus.RETURNED,
        note=f"{RETURN_OF_PREFIX}{original.id}",
        purchased_at=utc_now().date(),
        total_amount=(-total).quantize(Decimal("0.01")),
        idempotency_key=f"return:{utc_now().isoformat()}",
    )
    session.add(return_purchase_row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Return could not be created") from exc
    for original_item, quantity, returned_value in returned_lines:
        session.add(
            PurchaseItem(
                purchase_id=return_purchase_row.id,
                store_product_id=original_item.store_product_id,
                quantity=quantity,
                unit_cost=Decimal(0),
                line_total=(-returned_value).quantize(CENT),
                batch_number=f"RETURN:{original_item.id}",
                expiry_date=None,
            )
        )
    for store_product_id, allocations in allocations_by_product.items():
        try:
            await consume_allocations(
                session,
                context,
                store_product_id,
                allocations,
                InventoryMovementType.RETURN,
                reference_type="purchase_return",
                reference_id=original.id,
                commit=False,
            )
        except InsufficientStock as exc:
            await session.rollback()
            raise Conflict("Insufficient stock to process the return") from exc

    # An item received without a cost is intentionally valued at zero. Returning
    # it still removes the stock, but there is no supplier credit to add to the
    # non-zero ledger.
    if total != 0:
        await append_ledger_entry(
            session,
            context,
            original.supplier_id,
            "purchase_return",
            (-total).quantize(Decimal("0.01")),
            reference_type="purchase_return",
            reference_id=original.id,
            idempotency_key=f"purchase-return:{return_purchase_row.id}",
            commit=False,
            request_id=request_id,
        )
    record_audit(
        session,
        context,
        action="purchase.returned",
        entity_type="purchase",
        entity_id=original.id,
        request_id=request_id,
        after=redact({"return_purchase_id": str(return_purchase_row.id), "total": str(total)}),
    )
    await session.commit()
    return return_purchase_row


async def list_purchases(
    session: AsyncSession,
    context: RequestContext,
    *,
    status: PurchaseStatus | None = None,
    supplier_id: UUID | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Purchase], int]:
    scope: tuple[Any, ...] = (Purchase.organization_id == context.organization_id,)
    if context.store_id is not None:
        scope = (*scope, Purchase.store_id == context.store_id)
    if status is not None:
        scope = (*scope, Purchase.status == status)
    if supplier_id is not None:
        scope = (*scope, Purchase.supplier_id == supplier_id)
    total = await session.scalar(select(func.count()).select_from(Purchase).where(*scope))
    rows = list(
        await session.scalars(
            select(Purchase)
            .where(*scope)
            .order_by(Purchase.created_at.desc(), Purchase.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, int(total or 0)


async def get_purchase_with_items(
    session: AsyncSession, context: RequestContext, purchase_id: UUID
) -> tuple[Purchase, list[PurchaseItem]]:
    purchase = await load_purchase(session, context, purchase_id)
    return purchase, await _load_items(session, purchase.id)


def require_idempotency_key(idempotency_key: str | None) -> str:
    """Shared header guard; mirrors the FastAPI dependency in ``routers.purchasing``."""
    if not idempotency_key or not 16 <= len(idempotency_key.strip()) <= 128:
        raise ValidationError("Idempotency-Key must be between 16 and 128 characters")
    return idempotency_key.strip()
