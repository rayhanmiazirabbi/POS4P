from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.context import RequestContext
from app.dependencies import ContextDep, RequestIdDep, SessionDep, require_roles
from app.models import Organization, Role, Store
from app.schemas.base import Envelope
from app.schemas.organizations import (
    CurrentOrganizationResponse,
    OrganizationCreateRequest,
    OrganizationCreateResponse,
    OrganizationProfileResponse,
    OrganizationResponse,
    OrganizationSettings,
    OrganizationSettingsResponse,
    OrganizationSettingsUpdate,
    OrganizationUpdateRequest,
)
from app.schemas.stores import StoreResponse
from app.services import organizations as service

router = APIRouter(prefix="/organizations", tags=["Organizations"])

#: Only owners administer the tenant, mirroring ``organization.manage`` in
#: ``@pharmacy/permissions``.
OwnerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER))]


def _profile(organization: Organization) -> OrganizationProfileResponse:
    return OrganizationProfileResponse(
        **OrganizationResponse.model_validate(organization).model_dump(),
        settings=service.coerce_settings(OrganizationSettings, organization.settings),
    )


def _store(store: Store | None) -> StoreResponse | None:
    return None if store is None else StoreResponse.model_validate(store)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[OrganizationCreateResponse],
    summary="Create an organization and bootstrap its owner",
)
async def create_organization(
    payload: OrganizationCreateRequest,
    session: SessionDep,
    actor: service.ActorDep,
    request_id: RequestIdDep,
) -> Envelope[OrganizationCreateResponse]:
    organization, membership = await service.create_organization(
        session, actor, payload, request_id=request_id
    )
    return Envelope(
        data=OrganizationCreateResponse(
            organization=_profile(organization), role=membership.role, user_id=actor.id
        ),
        request_id=request_id,
    )


@router.get(
    "/current",
    response_model=Envelope[CurrentOrganizationResponse],
    summary="Resolve the tenant context behind the access token",
)
async def read_current_organization(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[CurrentOrganizationResponse]:
    organization, store = await service.current_context(session, context)
    return Envelope(
        data=CurrentOrganizationResponse(
            organization=OrganizationResponse.model_validate(organization),
            role=context.role,
            user_id=context.user_id,
            store_id=store.id if store else None,
            store=_store(store),
            settings=service.coerce_settings(OrganizationSettings, organization.settings),
        ),
        request_id=request_id,
    )


@router.get("/current/profile", response_model=Envelope[OrganizationProfileResponse])
async def read_profile(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[OrganizationProfileResponse]:
    organization = await service.get_organization(session, context)
    return Envelope(data=_profile(organization), request_id=request_id)


@router.patch("/current/profile", response_model=Envelope[OrganizationProfileResponse])
async def update_profile(
    payload: OrganizationUpdateRequest,
    session: SessionDep,
    context: OwnerDep,
    request_id: RequestIdDep,
) -> Envelope[OrganizationProfileResponse]:
    organization = await service.update_organization(
        session, context, payload, request_id=request_id
    )
    return Envelope(data=_profile(organization), request_id=request_id)


@router.get("/current/settings", response_model=Envelope[OrganizationSettingsResponse])
async def read_settings(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[OrganizationSettingsResponse]:
    settings = await service.read_settings(session, context)
    return Envelope(
        data=OrganizationSettingsResponse(
            organization_id=context.organization_id, settings=settings
        ),
        request_id=request_id,
    )


@router.patch("/current/settings", response_model=Envelope[OrganizationSettingsResponse])
async def update_settings(
    payload: OrganizationSettingsUpdate,
    session: SessionDep,
    context: OwnerDep,
    request_id: RequestIdDep,
) -> Envelope[OrganizationSettingsResponse]:
    settings = await service.update_settings(session, context, payload, request_id=request_id)
    return Envelope(
        data=OrganizationSettingsResponse(
            organization_id=context.organization_id, settings=settings
        ),
        request_id=request_id,
    )
