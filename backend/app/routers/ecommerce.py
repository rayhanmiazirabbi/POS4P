from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.dependencies import (
    ContextDep,
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.models import Role
from app.schemas.base import Envelope
from app.schemas.ecommerce import (
    ListingResponse,
    ListingUpsertRequest,
    PublicCatalogueItem,
    StorefrontResponse,
    StorefrontUpsertRequest,
)
from app.services import ecommerce as service

router = APIRouter(prefix="/ecommerce", tags=["Ecommerce"])

StoreManagerDep = Annotated[object, Depends(require_roles(Role.OWNER, Role.MANAGER))]


@router.get("/storefronts", response_model=Envelope[list[StorefrontResponse]])
async def list_storefronts(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[list[StorefrontResponse]]:
    rows = await service.list_storefronts(session, context)
    return Envelope(
        data=[StorefrontResponse.model_validate(row) for row in rows],
        request_id=request_id,
    )


@router.post(
    "/storefronts",
    response_model=Envelope[StorefrontResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upsert_storefront(
    payload: StorefrontUpsertRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StorefrontResponse]:
    storefront = await service.upsert_storefront(session, context, payload, request_id=request_id)
    return Envelope(data=StorefrontResponse.model_validate(storefront), request_id=request_id)


@router.put(
    "/products/{store_product_id}/listing",
    response_model=Envelope[ListingResponse],
    summary="Create or update the online listing overlay of a store product",
)
async def upsert_listing(
    store_product_id: UUID,
    payload: ListingUpsertRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[ListingResponse]:
    setting = await service.upsert_listing(
        session, context, store_product_id, payload, request_id=request_id
    )
    return Envelope(data=ListingResponse.model_validate(setting), request_id=request_id)


@router.get("/listings", response_model=Envelope[list[ListingResponse]])
async def list_listings(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    listed: Annotated[bool | None, Query()] = None,
) -> Envelope[list[ListingResponse]]:
    rows = await service.list_listings(session, context, listed=listed)
    return Envelope(
        data=[ListingResponse.model_validate(row) for row in rows],
        request_id=request_id,
    )


@router.get(
    "/storefronts/{slug}/catalogue",
    response_model=Envelope[list[PublicCatalogueItem]],
    summary="Public catalogue projection resolved from live store products",
)
async def public_catalogue(
    slug: str,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[list[PublicCatalogueItem]]:
    _storefront, items = await service.public_catalogue(session, context, slug)
    return Envelope(
        data=[PublicCatalogueItem.model_validate(item) for item in items],
        request_id=request_id,
    )
