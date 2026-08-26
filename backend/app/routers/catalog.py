from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import ContextDep, RequestIdDep, SessionDep, require_roles
from app.domains.catalog import ActiveIngredient, DosageForm, Manufacturer
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.catalog import (
    CatalogProductResponse,
    CatalogRevisionResponse,
    DosageFormCreateRequest,
    IngredientCreateRequest,
    ProductCreateRequest,
    ProductIngredientResponse,
    ProductUpdateRequest,
    ReferenceCreateRequest,
    ReferenceResponse,
    ReferenceUpdateRequest,
)
from app.services import catalog as service

router = APIRouter(prefix="/catalog", tags=["Catalog"])

#: The catalogue is shared reference data: every authenticated tenant may read it.
CatalogManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


def _reference(row: Manufacturer | ActiveIngredient | DosageForm) -> ReferenceResponse:
    return ReferenceResponse(
        id=row.id,
        name=row.name,
        country_code=getattr(row, "country_code", None),
        active=getattr(row, "active", True),
        created_at=row.created_at,
    )


async def _product_response(
    session: SessionDep, product_id: UUID
) -> CatalogProductResponse:
    detail = await service.product_detail(session, await service.load_product(session, product_id))
    return _product_body(detail)


def _product_body(detail: dict) -> CatalogProductResponse:
    product = detail["product"]
    return CatalogProductResponse(
        id=product.id,
        name=product.name,
        generic_name=product.generic_name,
        manufacturer_id=product.manufacturer_id,
        dosage_form_id=product.dosage_form_id,
        strength=product.strength,
        package_size=product.package_size,
        package_unit=product.package_unit,
        prescription_required=product.prescription_required,
        country_code=product.country_code,
        active=product.active,
        unit_price=product.unit_price,
        strip_price=product.strip_price,
        ingredients=[
            ProductIngredientResponse(
                active_ingredient_id=row.active_ingredient_id,
                strength=row.strength,
                unit=row.unit,
            )
            for row in detail["ingredients"]
        ],
        barcodes=detail["barcodes"],
        aliases=detail["aliases"],
        created_at=product.created_at,
    )


# --- manufacturers ----------------------------------------------------------


@router.get("/manufacturers", response_model=Envelope[Page[ReferenceResponse]])
async def list_manufacturers(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[Page[ReferenceResponse]]:
    rows = await service.list_manufacturers(session)
    return Envelope(
        data=Page(items=[_reference(row) for row in rows], total=len(rows)),
        request_id=request_id,
    )


@router.post(
    "/manufacturers",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[ReferenceResponse],
)
async def create_manufacturer(
    payload: ReferenceCreateRequest,
    session: SessionDep,
    context: CatalogManagerDep,
    request_id: RequestIdDep,
) -> Envelope[ReferenceResponse]:
    row = await service.create_manufacturer(session, context, payload, request_id=request_id)
    return Envelope(data=_reference(row), request_id=request_id)


@router.patch("/manufacturers/{manufacturer_id}", response_model=Envelope[ReferenceResponse])
async def update_manufacturer(
    manufacturer_id: UUID,
    payload: ReferenceUpdateRequest,
    session: SessionDep,
    context: CatalogManagerDep,
    request_id: RequestIdDep,
) -> Envelope[ReferenceResponse]:
    row = await service.update_manufacturer(
        session, context, manufacturer_id, payload, request_id=request_id
    )
    return Envelope(data=_reference(row), request_id=request_id)


# --- active ingredients -----------------------------------------------------


@router.get("/ingredients", response_model=Envelope[Page[ReferenceResponse]])
async def list_ingredients(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[Page[ReferenceResponse]]:
    rows = await service.list_active_ingredients(session)
    return Envelope(
        data=Page(items=[_reference(row) for row in rows], total=len(rows)),
        request_id=request_id,
    )


@router.post(
    "/ingredients",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[ReferenceResponse],
)
async def create_ingredient(
    payload: IngredientCreateRequest,
    session: SessionDep,
    context: CatalogManagerDep,
    request_id: RequestIdDep,
) -> Envelope[ReferenceResponse]:
    row = await service.create_active_ingredient(session, context, payload, request_id=request_id)
    return Envelope(data=_reference(row), request_id=request_id)


@router.patch("/ingredients/{ingredient_id}", response_model=Envelope[ReferenceResponse])
async def update_ingredient(
    ingredient_id: UUID,
    payload: ReferenceUpdateRequest,
    session: SessionDep,
    context: CatalogManagerDep,
    request_id: RequestIdDep,
) -> Envelope[ReferenceResponse]:
    row = await service.update_active_ingredient(
        session, context, ingredient_id, payload, request_id=request_id
    )
    return Envelope(data=_reference(row), request_id=request_id)


# --- dosage forms -----------------------------------------------------------


@router.get("/dosage-forms", response_model=Envelope[Page[ReferenceResponse]])
async def list_dosage_forms(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[Page[ReferenceResponse]]:
    rows = await service.list_dosage_forms(session)
    return Envelope(
        data=Page(items=[_reference(row) for row in rows], total=len(rows)),
        request_id=request_id,
    )


@router.post(
    "/dosage-forms",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[ReferenceResponse],
)
async def create_dosage_form(
    payload: DosageFormCreateRequest,
    session: SessionDep,
    context: CatalogManagerDep,
    request_id: RequestIdDep,
) -> Envelope[ReferenceResponse]:
    row = await service.create_dosage_form(session, context, payload, request_id=request_id)
    return Envelope(data=_reference(row), request_id=request_id)


@router.patch("/dosage-forms/{form_id}", response_model=Envelope[ReferenceResponse])
async def update_dosage_form(
    form_id: UUID,
    payload: ReferenceUpdateRequest,
    session: SessionDep,
    context: CatalogManagerDep,
    request_id: RequestIdDep,
) -> Envelope[ReferenceResponse]:
    row = await service.update_dosage_form(
        session, context, form_id, payload, request_id=request_id
    )
    return Envelope(data=_reference(row), request_id=request_id)


# --- products ---------------------------------------------------------------


@router.get("/products", response_model=Envelope[Page[CatalogProductResponse]])
async def search_products(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    q: Annotated[str | None, Query(max_length=120)] = None,
    country_code: Annotated[str | None, Query(alias="countryCode", min_length=2, max_length=2)] = None,
    prescription_required: Annotated[bool | None, Query(alias="prescriptionRequired")] = None,
    active: bool | None = None,
    order: Annotated[str, Query(pattern="^(newest|alpha)$")] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[CatalogProductResponse]]:
    items, total = await service.search_products(
        session,
        q=q,
        country_code=country_code,
        prescription_required=prescription_required,
        active=active,
        order=order,
        limit=limit,
        offset=offset,
    )
    pages = [
        await _product_response(session, product.id)
        for product in items
    ]
    return Envelope(data=Page(items=pages, total=total), request_id=request_id)


@router.post(
    "/products",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[CatalogProductResponse],
)
async def create_product(
    payload: ProductCreateRequest,
    session: SessionDep,
    context: CatalogManagerDep,
    request_id: RequestIdDep,
) -> Envelope[CatalogProductResponse]:
    product = await service.create_product(session, context, payload, request_id=request_id)
    return Envelope(data=await _product_response(session, product.id), request_id=request_id)


@router.get("/products/{product_id}", response_model=Envelope[CatalogProductResponse])
async def read_product(
    product_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[CatalogProductResponse]:
    return Envelope(data=await _product_response(session, product_id), request_id=request_id)


@router.patch("/products/{product_id}", response_model=Envelope[CatalogProductResponse])
async def update_product(
    product_id: UUID,
    payload: ProductUpdateRequest,
    session: SessionDep,
    context: CatalogManagerDep,
    request_id: RequestIdDep,
) -> Envelope[CatalogProductResponse]:
    product = await service.update_product(
        session, context, product_id, payload, request_id=request_id
    )
    return Envelope(data=await _product_response(session, product.id), request_id=request_id)


@router.get("/products/{product_id}/revisions", response_model=Envelope[list[CatalogRevisionResponse]])
async def list_revisions(
    product_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[list[CatalogRevisionResponse]]:
    revisions = await service.list_revisions(session, product_id)
    items = [
        CatalogRevisionResponse.model_validate(revision, from_attributes=True)
        for revision in revisions
    ]
    return Envelope(data=items, request_id=request_id)
