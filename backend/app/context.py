from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request

from app.models import Role


@dataclass(frozen=True)
class RequestContext:
    organization_id: UUID
    user_id: UUID
    role: Role
    store_id: UUID | None = None
    device_id: UUID | None = None


def require_context(request: Request) -> RequestContext:
    context = getattr(request.state, "context", None)
    if not isinstance(context, RequestContext):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Authentication required"})
    return context


def require_store_context(request: Request) -> RequestContext:
    context = require_context(request)
    if context.store_id is None:
        raise HTTPException(status_code=400, detail={"code": "STORE_CONTEXT_REQUIRED", "message": "Store context required"})
    return context
