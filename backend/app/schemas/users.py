from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.models import RecordStatus, Role
from app.schemas.base import ApiModel

#: Roles a staff account may be *created* with. ``owner`` is bootstrapped with the
#: organization and can afterwards only be granted by another owner, so it is not
#: offered here -- otherwise any manager could mint owners.
StaffRole = Literal["manager", "cashier", "inventory_staff"]

#: ``Membership.status`` in ``@pharmacy/types`` is ``'active' | 'invited'``. Invitations
#: are Stage 2, so MVP reports the two states an ``organization_users`` row can hold.
MembershipStatus = Literal["active", "inactive"]

DisplayName = Annotated[str, Field(min_length=2, max_length=160)]
#: Accepted loosely here and canonicalised by ``services.users.normalize_phone``;
#: the storage column is the single source of uniqueness.
PhoneNumber = Annotated[str, Field(min_length=6, max_length=32, pattern=r"^\+?[0-9][0-9\s().-]{4,30}$")]
Pin = Annotated[str, Field(min_length=4, max_length=6, pattern=r"^[0-9]+$")]
Reason = Annotated[str | None, Field(max_length=280)]


def validate_pin(value: str) -> str:
    """Reject a PIN made of one repeated digit; the keypad makes those trivial to shoulder-surf."""
    if len(set(value)) == 1:
        raise ValueError("PIN must not be a single repeated digit")
    return value


class UserCreateRequest(ApiModel):
    """No ``organizationId``: the tenant comes from the request context only.

    ``pin`` is optional so an administrator can create the account now and hand the
    PIN over later through the dedicated reset endpoint.
    """

    phone: PhoneNumber
    display_name: DisplayName
    role: StaffRole
    pin: Pin | None = None
    store_id: UUID | None = None

    @field_validator("pin")
    @classmethod
    def _strong_pin(cls, value: str | None) -> str | None:
        return None if value is None else validate_pin(value)


class UserUpdateRequest(ApiModel):
    display_name: DisplayName | None = None
    phone: PhoneNumber | None = None


class UserRoleUpdateRequest(ApiModel):
    """``role`` accepts ``owner`` too; who may grant it is enforced in the service."""

    role: Role
    reason: Reason = None


class UserStatusUpdateRequest(ApiModel):
    status: RecordStatus
    reason: Reason = None


class PinSetRequest(ApiModel):
    pin: Pin

    @field_validator("pin")
    @classmethod
    def _strong_pin(cls, value: str) -> str:
        return validate_pin(value)


class StoreAssignmentRequest(ApiModel):
    store_id: UUID


class UserResponse(ApiModel):
    """Field-for-field mirror of the ``User`` type in ``@pharmacy/types``.

    Deliberately has no PIN field: neither the PIN nor its hash ever leaves the API.
    """

    id: UUID
    phone: str
    display_name: str
    status: RecordStatus
    created_at: datetime


class MembershipResponse(ApiModel):
    """Mirrors ``Membership`` in ``@pharmacy/types``."""

    user_id: UUID
    organization_id: UUID
    role: Role
    status: MembershipStatus


class StoreMembershipResponse(ApiModel):
    """Mirrors ``StoreMembership`` in ``@pharmacy/types``."""

    user_id: UUID
    store_id: UUID
    role: Role
    status: MembershipStatus


class UserProfileResponse(UserResponse):
    """A staff member as the roster and detail screens need them.

    ``pinSet`` lets the client prompt for PIN setup without ever seeing the secret.
    """

    membership: MembershipResponse
    store_memberships: list[StoreMembershipResponse]
    pin_set: bool


class PinStatusResponse(ApiModel):
    user_id: UUID
    pin_set: bool
