from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from statistics import median as median_of
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.ai import AIConfirmation, AIJob, AIJobStatus
from app.domains.products import PharmacyProduct, StoreProduct
from app.domains.sales import Sale, SaleStatus
from app.errors import Conflict, Forbidden, NotFound, ValidationError
from app.models import Role
from app.schemas.ai import PurchaseDraftRequest
from app.security import utc_now
from app.services import reports as reports_service
from app.services.ai_providers import (
    MODEL_VERSION,
    PROVIDER_NAME,
    InvoiceOCRAdapter,
    VoiceCartAdapter,
)
from app.services.audit import enqueue_outbox, record_audit, redact
from app.services.inventory import expiring_batches, low_stock_products
from app.services.purchasing import create_purchase

#: Below this overall confidence a job lands in ``needs_review`` and cannot
#: touch business state until a human accepts it.
REVIEW_THRESHOLD = Decimal("0.90")

WRITER_ROLES = frozenset({Role.OWNER, Role.MANAGER})

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_JOB_TYPES = frozenset(
    {
        "invoice_ocr",
        "voice_cart",
        "reorder_suggestions",
        "expiry_suggestions",
        "report_query",
        "anomaly_detection",
    }
)

#: The complete universe of report tools the natural-language query may reach.
#: A question that maps to none of these is rejected -- the assistant never
#: runs free-form SQL or sees tables outside this list.
REPORT_TOOLS: dict[str, str] = {
    "today_summary": r"today|sales summary|how (much|many) sold|revenue",
    "low_stock": r"low stock|running out|below minimum|reorder level",
    "expiry": r"expir|out of date|shelf",
    "top_products": r"top (products|sellers|items)|best.?selling",
}

#: Modified z-score threshold on daily totals (median/MAD based, so one wild
#: day does not hide itself by inflating the spread the way a plain z-score
#: would on a short window).
_ANOMALY_MODIFIED_Z = Decimal("3.5")
_MIN_SAMPLE_DAYS = 5


def _normalize(text: str) -> str:
    return " ".join(_TOKEN_RE.findall(text.lower()))


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def match_score(query: str, candidate_names: list[str]) -> Decimal:
    """Token-overlap score in [0, 1]; exact normalized equality wins outright."""
    query_normalized = _normalize(query)
    query_tokens = _tokens(query)
    if not query_tokens:
        return Decimal("0.20")
    for candidate in candidate_names:
        if _normalize(candidate) == query_normalized:
            return Decimal(1)
    best = 0.0
    for candidate in candidate_names:
        candidate_tokens = _tokens(candidate)
        if not candidate_tokens:
            continue
        overlap = len(query_tokens & candidate_tokens)
        best = max(best, overlap / len(query_tokens))
    return Decimal(str(round(min(best, 1.0), 2)))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    return str(value)


# --- product matching ---------------------------------------------------------


async def org_products(session: AsyncSession, organization_id: UUID) -> list[PharmacyProduct]:
    return list(
        await session.scalars(
            select(PharmacyProduct)
            .where(
                PharmacyProduct.organization_id == organization_id,
                PharmacyProduct.active.is_(True),
            )
            .order_by(PharmacyProduct.name)
        )
    )


async def store_products_with_names(
    session: AsyncSession, context: RequestContext
) -> list[tuple[StoreProduct, str]]:
    rows = await session.execute(
        select(StoreProduct, PharmacyProduct.name)
        .join(PharmacyProduct, PharmacyProduct.id == StoreProduct.pharmacy_product_id)
        .where(
            StoreProduct.organization_id == context.organization_id,
            StoreProduct.store_id == context.store_id,
            StoreProduct.active.is_(True),
        )
        .order_by(StoreProduct.sku)
    )
    return [(store_product, name) for store_product, name in rows]


async def catalogue_candidates(
    session: AsyncSession,
    context: RequestContext,
    query: str,
    *,
    limit: int = 3,
) -> tuple[list[dict], Decimal]:
    """Top catalogue matches scoped to the caller's organization only.

    Every candidate id is read from a query filtered on ``organization_id``; the
    model never receives, and the result can never contain, another tenant's
    products.
    """
    products = await org_products(session, context.organization_id)
    scored = sorted(
        (
            (match_score(query, [product.name]), product)
            for product in products
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )[:limit]
    candidates = [
        {
            "pharmacy_product_id": str(product.id),
            "name": product.name,
            "score": score,
        }
        for score, product in scored
        if score > 0
    ]
    top_confidence = max((score for score, _ in scored), default=Decimal(0))
    return candidates, top_confidence


# --- job handlers -------------------------------------------------------------


async def _run_invoice_ocr(
    session: AsyncSession, context: RequestContext, payload: dict
) -> tuple[dict, Decimal]:
    text = payload.get("text")
    lines = InvoiceOCRAdapter().extract_lines(text if isinstance(text, str) else "")
    results = []
    for line in lines:
        candidates, top = await catalogue_candidates(session, context, line.description)
        line_confidence = min(line.confidence, top) if candidates else line.confidence * Decimal("0.5")
        results.append(
            {
                "description": line.description,
                "quantity": line.quantity,
                "unit_cost": line.unit_cost,
                "confidence": line_confidence,
                "candidates": candidates,
            }
        )
    overall = min(
        (Decimal(str(line["confidence"])) for line in results), default=Decimal(0)
    )
    unmatched = sum(1 for line in results if not line["candidates"])
    return {"lines": results, "unmatched_count": unmatched}, overall


async def _run_voice_cart(
    session: AsyncSession, context: RequestContext, payload: dict
) -> tuple[dict, Decimal]:
    transcript = payload.get("transcript")
    items = VoiceCartAdapter().parse_items(transcript if isinstance(transcript, str) else "")
    store_rows = await store_products_with_names(session, context)
    name_by_id = {store_product.id: name for store_product, name in store_rows}
    results = []
    for item in items:
        best_product = None
        best_score = Decimal(0)
        for store_product, name in store_rows:
            score = match_score(item.name, [name, store_product.sku or ""])
            if score > best_score:
                best_score, best_product = score, store_product
        if best_product is None:
            continue
        confidence = item.confidence * best_score
        results.append(
            {
                "store_product_id": str(best_product.id),
                "name": name_by_id[best_product.id],
                "sku": best_product.sku,
                "quantity": item.quantity,
                "confidence": confidence,
            }
        )
    overall = (
        min((Decimal(str(item["confidence"])) for item in results), default=Decimal(0))
        if results
        else Decimal(0)
    )
    return {"items": results}, overall


async def _run_reorder_suggestions(
    session: AsyncSession, context: RequestContext, payload: dict
) -> tuple[dict, Decimal]:
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    low = await low_stock_products(session, context, context.store_id)
    names = {
        store_product.id: name for store_product, name in await store_products_with_names(session, context)
    }
    suggestions = []
    for product, available in low:
        minimum = Decimal(product.minimum_stock)
        suggestions.append(
            {
                "store_product_id": str(product.id),
                "name": names.get(product.id, product.sku),
                "sku": product.sku,
                "available": available,
                "minimum_stock": minimum,
                "suggested_order_quantity": max(minimum - available, Decimal(0)),
            }
        )
    return {"suggestions": suggestions}, Decimal("1.00")


async def _run_expiry_suggestions(
    session: AsyncSession, context: RequestContext, payload: dict
) -> tuple[dict, Decimal]:
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    within_days = int(payload.get("within_days", 30))
    batches = await expiring_batches(session, context, context.store_id, within_days=within_days)
    warnings = [
        {
            "store_product_id": str(store_product.id),
            "batch_number": batch.batch_number,
            "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
            "available": available,
        }
        for store_product, batch, available in batches
    ]
    return {"warnings": warnings}, Decimal("1.00")


async def _run_report_query(
    session: AsyncSession, context: RequestContext, payload: dict
) -> tuple[dict, Decimal]:
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValidationError("A report question is required")
    tool = next(
        (name for name, pattern in REPORT_TOOLS.items() if re.search(pattern, question.lower())),
        None,
    )
    if tool is None:
        raise ValidationError(
            f"Question does not map to an allow-listed report tool: {sorted(REPORT_TOOLS)}",
            code="VALIDATION_ERROR",
        )
    data: Any
    if tool == "today_summary":
        data = await reports_service.today_metrics(session, context)
        if not reports_service.can_see_profit(context):
            data.profit = None
    elif tool == "low_stock":
        data = await reports_service.low_stock(session, context)
    elif tool == "expiry":
        data = await reports_service.expiry_warnings(
            session, context, within_days=int(payload.get("within_days", 30))
        )
    else:
        data = await reports_service.top_products(session, context, limit=10)
    return {"tool": tool, "data": _json_safe(data)}, Decimal("1.00")


async def _run_anomaly_detection(
    session: AsyncSession, context: RequestContext, payload: dict
) -> tuple[dict, Decimal]:
    """Statistical scan of the branch's daily sales for outlier behaviour.

    Deterministic and local: z-scores over daily totals plus void/discount rate
    checks. Findings are alerts only -- nothing here changes business state.
    """
    window_days = min(int(payload.get("window_days", 30)), 60)
    since = utc_now() - timedelta(days=window_days)
    rows = await session.execute(
        select(func.date(Sale.created_at), func.count(), func.coalesce(func.sum(Sale.total), 0))
        .where(
            Sale.organization_id == context.organization_id,
            Sale.store_id == context.store_id,
            Sale.created_at >= since,
        )
        .group_by(func.date(Sale.created_at))
        .order_by(func.date(Sale.created_at))
    )
    daily = [(day, int(count), Decimal(str(total))) for day, count, total in rows.all()]

    findings: list[dict] = []
    totals = [total for _, _, total in daily]
    if len(totals) >= _MIN_SAMPLE_DAYS:
        median = Decimal(str(median_of(totals)))
        mad = Decimal(str(median_of([abs(total - median) for total in totals])))
        for day, count, total in daily:
            if mad == 0:
                if total != median:
                    findings.append(
                        {
                            "kind": "sales_total_outlier",
                            "day": day,
                            "detail": {"total": total, "median_day_total": median},
                        }
                    )
                continue
            modified_z = Decimal("0.6745") * (total - median) / mad
            if abs(modified_z) >= _ANOMALY_MODIFIED_Z:
                findings.append(
                    {
                        "kind": "sales_total_outlier",
                        "day": day,
                        "detail": {
                            "modified_z_score": round(float(modified_z), 2),
                            "total": total,
                        },
                    }
                )

    void_count = await session.scalar(
        select(func.count())
        .select_from(Sale)
        .where(
            Sale.organization_id == context.organization_id,
            Sale.store_id == context.store_id,
            Sale.status == SaleStatus.VOIDED,
            Sale.created_at >= since,
        )
    )
    total_sales = sum(count for _, count, _ in daily)
    if total_sales > 0 and void_count is not None and void_count / max(total_sales, 1) > 0.2:
        findings.append(
            {
                "kind": "elevated_void_rate",
                "day": None,
                "detail": {"voided": int(void_count), "sales": total_sales},
            }
        )

    result = {"findings": findings, "window_days": window_days, "days_observed": len(daily)}
    if findings:
        enqueue_outbox(
            session,
            organization_id=context.organization_id,
            event_type="ai.anomaly_alert",
            aggregate_type="ai_job",
            aggregate_id=context.organization_id,
            payload={"findings": _json_safe(findings)},
        )
    return result, Decimal("1.00")


_HANDLERS = {
    "invoice_ocr": _run_invoice_ocr,
    "voice_cart": _run_voice_cart,
    "reorder_suggestions": _run_reorder_suggestions,
    "expiry_suggestions": _run_expiry_suggestions,
    "report_query": _run_report_query,
    "anomaly_detection": _run_anomaly_detection,
}


# --- job lifecycle ------------------------------------------------------------


async def load_job(session: AsyncSession, context: RequestContext, job_id: UUID) -> AIJob:
    """A job of another tenant does not exist for the caller."""
    job = await session.get(AIJob, job_id)
    if job is None or job.organization_id != context.organization_id:
        raise NotFound("AI job not found")
    return job


async def list_jobs(
    session: AsyncSession, context: RequestContext, *, limit: int = 25, offset: int = 0
) -> tuple[list[AIJob], int]:
    scope = (AIJob.organization_id == context.organization_id,)
    total = await session.scalar(select(func.count()).select_from(AIJob).where(*scope))
    rows = list(
        await session.scalars(
            select(AIJob).where(*scope).order_by(AIJob.created_at.desc()).limit(limit).offset(offset)
        )
    )
    return rows, int(total or 0)


async def create_job(
    session: AsyncSession,
    context: RequestContext,
    payload,
    *,
    idempotency_key: str,
    request_id: str,
) -> AIJob:
    """Create and synchronously run an AI job; replaying the key returns the original."""
    if payload.job_type not in _JOB_TYPES:
        raise ValidationError(f"Unknown job type; expected one of {sorted(_JOB_TYPES)}")
    handler = _HANDLERS[payload.job_type]
    if payload.job_type in (
        "voice_cart",
        "reorder_suggestions",
        "expiry_suggestions",
        "anomaly_detection",
    ) and context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")

    existing = await session.scalar(
        select(AIJob).where(
            AIJob.organization_id == context.organization_id,
            AIJob.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing

    job = AIJob(
        organization_id=context.organization_id,
        job_type=payload.job_type,
        status=AIJobStatus.RUNNING,
        input_reference=_json_safe(payload.input),
        provider=PROVIDER_NAME,
        model_version=MODEL_VERSION,
        idempotency_key=idempotency_key,
        created_at=utc_now(),
    )
    session.add(job)
    try:
        await session.flush()
    except Exception:
        await session.rollback()
        raise
    record_audit(
        session,
        context,
        action="ai.job_created",
        entity_type="ai_job",
        entity_id=job.id,
        request_id=request_id,
        after={"job_type": job.job_type},
    )
    try:
        result, confidence = await handler(session, context, payload.input)
        job.result = _json_safe(result)
        job.confidence = confidence
        job.status = (
            AIJobStatus.NEEDS_REVIEW if confidence < REVIEW_THRESHOLD else AIJobStatus.SUCCEEDED
        )
    except Exception as exc:  # noqa: BLE001 - provider failures become job state
        job.status = AIJobStatus.FAILED
        job.error = str(exc)[:2000]
        if isinstance(exc, (ValidationError, Forbidden, NotFound)):
            # Caller errors are answers, not failures of the provider run.
            job.error = exc.message[:2000]
            job.result = None
    job.completed_at = utc_now()
    record_audit(
        session,
        context,
        action="ai.job_completed",
        entity_type="ai_job",
        entity_id=job.id,
        request_id=request_id,
        after={"status": job.status.value, "confidence": _json_safe(job.confidence)},
    )
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return job


async def confirm_job(
    session: AsyncSession,
    context: RequestContext,
    job_id: UUID,
    payload,
    *,
    request_id: str,
) -> AIConfirmation:
    """Record the human decision on an AI result.

    Only owners and managers confirm: an assistant suggestion becomes real work
    exclusively through a person with authority to do that work themselves.
    """
    if context.role not in WRITER_ROLES:
        raise Forbidden("Only owners and managers confirm AI results")
    job = await load_job(session, context, job_id)
    if job.status not in (AIJobStatus.SUCCEEDED, AIJobStatus.NEEDS_REVIEW):
        raise Conflict(f"A {job.status.value} job cannot be confirmed")
    confirmation = AIConfirmation(
        organization_id=context.organization_id,
        job_id=job.id,
        confirmed_by_user_id=context.user_id,
        decision=payload.decision,
        notes=payload.notes,
        created_at=utc_now(),
    )
    session.add(confirmation)
    record_audit(
        session,
        context,
        action="ai.confirmed",
        entity_type="ai_job",
        entity_id=job.id,
        request_id=request_id,
        after=redact({"decision": payload.decision}),
    )
    await session.commit()
    return confirmation


async def has_accepted_confirmation(session: AsyncSession, job_id: UUID) -> bool:
    accepted = await session.scalar(
        select(AIConfirmation.id).where(
            AIConfirmation.job_id == job_id, AIConfirmation.decision == "accepted"
        )
    )
    return accepted is not None


async def create_purchase_draft_from_job(
    session: AsyncSession,
    context: RequestContext,
    job_id: UUID,
    payload: PurchaseDraftRequest,
    *,
    request_id: str,
):
    """Turn a human-accepted OCR extraction into a DRAFT purchase.

    The gate is explicit: the job must be an invoice extraction carrying an
    ``accepted`` confirmation, every selected line must match a candidate the
    model actually proposed for that line, and the purchase stays a draft until a
    manager confirms it through the ordinary purchasing flow.
    """
    from app.schemas.purchasing import PurchaseCreateRequest, PurchaseItemCreate

    job = await load_job(session, context, job_id)
    if job.job_type != "invoice_ocr":
        raise Conflict("Only invoice extractions can become purchases")
    if job.status not in (AIJobStatus.SUCCEEDED, AIJobStatus.NEEDS_REVIEW):
        raise Conflict(f"A {job.status.value} job cannot be converted")
    if not await has_accepted_confirmation(session, job.id):
        raise Forbidden("The extraction must be accepted by a manager before ordering")
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")

    lines = ((job.result or {}).get("lines")) or []
    by_index = {index: line for index, line in enumerate(lines)}

    org_product_ids = {
        product.id for product in await org_products(session, context.organization_id)
    }
    store_product_by_pharmacy_id = {
        store_product.pharmacy_product_id: store_product
        for store_product, _name in await store_products_with_names(session, context)
    }

    items: list[PurchaseItemCreate] = []
    for selection in payload.selections:
        line = by_index.get(selection.line_index)
        if line is None:
            raise ValidationError(f"Line {selection.line_index} is not part of this extraction")
        offered = {
            UUID(candidate["pharmacy_product_id"]) for candidate in line.get("candidates", [])
        }
        if selection.pharmacy_product_id not in offered:
            raise ValidationError("Selection must match a candidate the model proposed")
        if selection.pharmacy_product_id not in org_product_ids:
            raise Forbidden("Product does not belong to this organization")
        store_product = store_product_by_pharmacy_id.get(selection.pharmacy_product_id)
        if store_product is None:
            raise ValidationError(
                "The selected product is not stocked at this branch; enable it first"
            )
        quantity = Decimal(str(line.get("quantity") or 0))
        unit_cost = Decimal(str(line.get("unit_cost") or 0))
        if quantity <= 0 or unit_cost <= 0:
            raise ValidationError(f"Line {selection.line_index} is not orderable as extracted")
        items.append(
            PurchaseItemCreate(
                store_product_id=store_product.id,
                quantity=quantity,
                unit_cost=unit_cost,
                batch_number=f"OCR-{job.id.hex[:8]}-{selection.line_index}",
                expiry_date=None,
            )
        )
    if not items:
        raise ValidationError("At least one line must be selected")

    purchase_request = PurchaseCreateRequest(
        supplier_id=payload.supplier_id,
        invoice_number=None,
        note=f"AI draft from job {job.id}",
        purchased_at=utc_now().date(),
        items=items,
    )
    purchase = await create_purchase(session, context, purchase_request, request_id=request_id)
    return purchase
