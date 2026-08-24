from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.context import RequestContext
from app.dependencies import (
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.models import Role
from app.schemas.audit import AuditLogResponse, AuditSearchRequest
from app.schemas.base import Envelope, Page
from app.services import audit as service

router = APIRouter(prefix="/audit", tags=["Audit"])

OwnerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER))]

EXPORT_COLUMNS = (
    "id",
    "created_at",
    "action",
    "entity_type",
    "entity_id",
    "actor_user_id",
    "store_id",
    "device_id",
    "request_id",
)


def _log_response(entry) -> AuditLogResponse:
    return AuditLogResponse(
        id=entry.id,
        action=entry.action,
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        actor_user_id=entry.actor_user_id,
        device_id=entry.device_id,
        store_id=entry.store_id,
        request_id=entry.request_id,
        before_data=entry.before_data,
        after_data=entry.after_data,
        created_at=entry.created_at,
    )


@router.get("/logs", response_model=Envelope[Page[AuditLogResponse]])
async def search_logs(
    session: SessionDep,
    context: OwnerDep,
    request_id: RequestIdDep,
    action: Annotated[str | None, Query(max_length=120)] = None,
    entity_type: Annotated[str | None, Query(alias="entityType", max_length=120)] = None,
    entity_id: Annotated[UUID | None, Query(alias="entityId")] = None,
    actor_user_id: Annotated[UUID | None, Query(alias="actorUserId")] = None,
    date_from: Annotated[datetime | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[datetime | None, Query(alias="dateTo")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[AuditLogResponse]]:
    """Searchable audit trail for owners: who did what, where, and when."""
    rows, total = await service.search_logs(
        session,
        context,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        date_from=date_from,
        date_to=date_to,
        q=q,
        limit=limit,
        offset=offset,
    )
    return Envelope(
        data=Page(items=[_log_response(row) for row in rows], total=total),
        request_id=request_id,
    )


@router.post("/logs/export", response_class=Response)
async def export_logs(
    payload: AuditSearchRequest,
    session: SessionDep,
    context: OwnerDep,
) -> Response:
    """CSV export of the filtered trail, capped at the export row ceiling."""
    rows = await service.export_logs(session, context, payload)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                str(row.id),
                row.created_at.isoformat(),
                row.action,
                row.entity_type,
                str(row.entity_id) if row.entity_id else "",
                str(row.actor_user_id) if row.actor_user_id else "",
                str(row.store_id) if row.store_id else "",
                str(row.device_id) if row.device_id else "",
                row.request_id,
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-export.csv"'},
    )


@router.get("/logs/verify", response_model=Envelope[dict])
async def verify_logs(
    session: SessionDep,
    context: OwnerDep,
    request_id: RequestIdDep,
) -> Envelope[dict]:
    """Report audit rows whose tamper-evidence signature no longer matches."""
    tampered = await service.verify_signatures(session, context)
    return Envelope(
        data={"tampered": [str(entry_id) for entry_id in tampered]},
        request_id=request_id,
    )


@router.post("/logs/prune", response_model=Envelope[dict])
async def prune_logs(
    session: SessionDep,
    context: OwnerDep,
    request_id: RequestIdDep,
) -> Envelope[dict]:
    """Delete this tenant's entries past the retention window."""
    pruned = await service.prune_expired(session, context)
    return Envelope(data={"pruned": pruned}, request_id=request_id)
