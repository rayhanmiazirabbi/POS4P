from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domains.ecommerce import Storefront
from app.errors import ValidationError
from app.routers.purchasing import require_idempotency_key
from app.schemas.base import Envelope
from app.schemas.ecommerce import PublicCatalogueItem
from app.schemas.orders import OrderCreateRequest, OrderResponse
from app.services import ecommerce as ecommerce_service
from app.services import orders as orders_service

router = APIRouter(
    prefix="/storefronts",
    tags=["Storefront"],
    responses={401: {"description": "Never returned: these routes are public"}},
)


async def _storefront(
    organization_slug: str, slug: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> Storefront:
    return await ecommerce_service.resolve_public_storefront(session, organization_slug, slug)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


RequestId = Annotated[str, Depends(_request_id)]


@router.get(
    "/{organization_slug}/{slug}/catalogue",
    response_model=Envelope[list[PublicCatalogueItem]],
    summary="Public catalogue of an enabled storefront; no authentication",
)
async def public_catalogue(
    storefront: Annotated[Storefront, Depends(_storefront)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request_id: RequestId,
) -> Envelope[list[PublicCatalogueItem]]:
    items = await ecommerce_service.catalogue_items(session, storefront)
    return Envelope(
        data=[PublicCatalogueItem.model_validate(item) for item in items],
        request_id=request_id,
    )


@router.post(
    "/{organization_slug}/{slug}/orders",
    response_model=Envelope[OrderResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Guest checkout against a storefront; requires an Idempotency-Key",
)
async def guest_checkout(
    payload: OrderCreateRequest,
    storefront: Annotated[Storefront, Depends(_storefront)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request_id: RequestId,
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
) -> Envelope[OrderResponse]:
    if not storefront.store_id:  # pragma: no cover - store_id is non-nullable
        raise ValidationError("Storefront has no branch", code="STORE_CONTEXT_REQUIRED")
    order, order_items, history = await orders_service.create_guest_order(
        session,
        organization_id=storefront.organization_id,
        store_id=storefront.store_id,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    from app.routers.orders import _build_response

    return Envelope(
        data=_build_response(order, order_items, history),
        request_id=request_id,
    )

