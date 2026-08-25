from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import ContextDep, RequestIdDep, SessionDep, require_roles
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.products import (
    PharmacyProductCreateRequest,
    PharmacyProductResponse,
    PharmacyProductStatusRequest,
    PharmacyProductUpdateRequest,
    ShelfItemResponse,
    StoreProductEnableRequest,
    StoreProductPriceResponse,
    StoreProductResponse,
    StoreProductStatusRequest,
    StoreProductUpdateRequest,
)
from app.services import products as service
from app.services.stores import load_store, load_current_store

router = APIRouter(prefix="/products", tags=["Products"])

#: Product administration maps onto owners and managers only; inventory staff
#: keep read access to the catalogue they pick from.
ProductManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


@router.get(
    "",
    response_model=Envelope[Page[PharmacyProductResponse]],
    summary="List the organization's pharmacy products",
)
async def list_pharmacy_products(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    includeInactive: Annotated[bool, Query()] = False,
) -> Envelope[Page[PharmacyProductResponse]]:
    products = await service.list_pharmacy_products(
        session, context, include_inactive=includeInactive
    )
    items = [PharmacyProductResponse.model_validate(p) for p in products]
    return Envelope(data=Page(items=items, total=len(items)), request_id=request_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[PharmacyProductResponse],
    summary="Create a custom or catalogue-linked product",
)
async def create_pharmacy_product(
    payload: PharmacyProductCreateRequest,
    session: SessionDep,
    context: ProductManagerDep,
    request_id: RequestIdDep,
) -> Envelope[PharmacyProductResponse]:
    product = await service.create_pharmacy_product(session, context, payload, request_id=request_id)
    return Envelope(data=PharmacyProductResponse.model_validate(product), request_id=request_id)


@router.get("/current", response_model=Envelope[Page[ShelfItemResponse]])
async def list_current_store_products(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    includeInactive: Annotated[bool, Query()] = False,
) -> Envelope[Page[ShelfItemResponse]]:
    """Shelf list for the branch the token is pinned to.

    **Declared before ``/{product_id}``, and it has to stay there.** FastAPI matches
    in declaration order, so with the literal route second every request for this
    path was handed to ``read_pharmacy_product``, which tried to parse ``"current"``
    as a UUID and answered 422. This is the list every counter loads at startup, so
    the shelf was empty on all three shells -- and the clients each reported it as
    "shelf is empty or unavailable", which reads like a shop with no stock rather
    than a broken route. No test covered the endpoint, so nothing said otherwise.
    """
    store = await load_current_store(session, context)
    rows = await service.list_shelf(session, context, store, include_inactive=includeInactive)
    # The product's name and barcode are folded in rather than left a join away:
    # this list is what a device caches to sell offline, and a scan it has to ask
    # the server about is a scan that does not work during an outage.
    items = [
        ShelfItemResponse.model_validate(
            {
                **StoreProductResponse.model_validate(shelf_row).model_dump(),
                "name": product.name,
                "barcode": product.barcode,
            }
        )
        for shelf_row, product in rows
    ]
    return Envelope(data=Page(items=items, total=len(items)), request_id=request_id)


@router.post(
    "/current",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[StoreProductResponse],
    summary="Enable a pharmacy product on the current branch's shelf",
)
async def enable_current_store_product(
    payload: StoreProductEnableRequest,
    session: SessionDep,
    context: ProductManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StoreProductResponse]:
    """Session-scoped twin of ``POST /stores/{store_id}``.

    The web shell manages the branch it is signed into and knows no other store id,
    so it posts here; the numbered route stays for owners working across branches.
    """
    store = await load_current_store(session, context)
    row, _created = await service.enable_store_product(session, context, store, payload, request_id=request_id)
    return Envelope(data=StoreProductResponse.model_validate(row), request_id=request_id)


@router.get("/{product_id}", response_model=Envelope[PharmacyProductResponse])
async def read_pharmacy_product(
    product_id: UUID, session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[PharmacyProductResponse]:
    product = await service.load_pharmacy_product(session, context, product_id)
    return Envelope(data=PharmacyProductResponse.model_validate(product), request_id=request_id)


@router.patch("/{product_id}", response_model=Envelope[PharmacyProductResponse])
async def update_pharmacy_product(
    product_id: UUID,
    payload: PharmacyProductUpdateRequest,
    session: SessionDep,
    context: ProductManagerDep,
    request_id: RequestIdDep,
) -> Envelope[PharmacyProductResponse]:
    product = await service.update_pharmacy_product(
        session, context, product_id, payload, request_id=request_id
    )
    return Envelope(data=PharmacyProductResponse.model_validate(product), request_id=request_id)


@router.patch("/{product_id}/status", response_model=Envelope[PharmacyProductResponse])
async def update_pharmacy_product_status(
    product_id: UUID,
    payload: PharmacyProductStatusRequest,
    session: SessionDep,
    context: ProductManagerDep,
    request_id: RequestIdDep,
) -> Envelope[PharmacyProductResponse]:
    """Deactivation is a soft delete; historical references are never removed."""
    product = await service.update_pharmacy_product_status(
        session, context, product_id, payload, request_id=request_id
    )
    return Envelope(data=PharmacyProductResponse.model_validate(product), request_id=request_id)


@router.get(
    "/stores/{store_id}",
    response_model=Envelope[Page[StoreProductResponse]],
    summary="List a store's enabled shelf products",
)
async def list_store_products(
    store_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    includeInactive: Annotated[bool, Query()] = False,
) -> Envelope[Page[StoreProductResponse]]:
    store = await load_store(session, context, store_id)
    rows = await service.list_store_products(session, context, store, include_inactive=includeInactive)
    items = [StoreProductResponse.model_validate(row) for row in rows]
    return Envelope(data=Page(items=items, total=len(items)), request_id=request_id)


@router.post(
    "/stores/{store_id}",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[StoreProductResponse],
    summary="Enable a pharmacy product on a store shelf",
)
async def enable_store_product(
    store_id: UUID,
    payload: StoreProductEnableRequest,
    session: SessionDep,
    context: ProductManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StoreProductResponse]:
    store = await load_store(session, context, store_id)
    row, _created = await service.enable_store_product(
        session, context, store, payload, request_id=request_id
    )
    return Envelope(data=StoreProductResponse.model_validate(row), request_id=request_id)


@router.patch("/stores/{store_id}/{row_id}", response_model=Envelope[StoreProductResponse])
async def update_store_product(
    store_id: UUID,
    row_id: UUID,
    payload: StoreProductUpdateRequest,
    session: SessionDep,
    context: ProductManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StoreProductResponse]:
    store = await load_store(session, context, store_id)
    row = await service.update_store_product(
        session, context, store, row_id, payload, request_id=request_id
    )
    return Envelope(data=StoreProductResponse.model_validate(row), request_id=request_id)


@router.patch(
    "/stores/{store_id}/{row_id}/status", response_model=Envelope[StoreProductResponse]
)
async def update_store_product_status(
    store_id: UUID,
    row_id: UUID,
    payload: StoreProductStatusRequest,
    session: SessionDep,
    context: ProductManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StoreProductResponse]:
    store = await load_store(session, context, store_id)
    row = await service.update_store_product_status(
        session, context, store, row_id, payload, request_id=request_id
    )
    return Envelope(data=StoreProductResponse.model_validate(row), request_id=request_id)


@router.get(
    "/stores/{store_id}/{row_id}/prices",
    response_model=Envelope[Page[StoreProductPriceResponse]],
    summary="Price history for a shelf product (append-only)",
)
async def list_price_history(
    store_id: UUID,
    row_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[Page[StoreProductPriceResponse]]:
    store = await load_store(session, context, store_id)
    history = await service.list_price_history(session, context, store, row_id)
    items = [StoreProductPriceResponse.model_validate(row) for row in history]
    return Envelope(data=Page(items=items, total=len(items)), request_id=request_id)
