from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.models import DeviceStatus, Role
from app.schemas.base import ApiModel
from app.schemas.users import PhoneNumber, Pin, UserResponse

#: What an OTP is being issued for. Signup and login share a code path -- the
#: response never differs between them, so a caller cannot use the endpoint to
#: discover whether a phone number is already registered.
ChallengePurpose = Literal["login", "signup", "phone_change"]

DeviceKey = Annotated[str, Field(min_length=8, max_length=160)]
DeviceName = Annotated[str, Field(min_length=1, max_length=120)]
OtpCode = Annotated[str, Field(min_length=4, max_length=10, pattern=r"^[0-9]+$")]


class DeviceClaim(ApiModel):
    """Optional device identity attached to a login.

    ``deviceKey`` is generated and persisted by the client install, so the same
    tablet reuses one row instead of accumulating a device per login.
    """

    device_key: DeviceKey
    device_name: DeviceName


class OtpRequest(ApiModel):
    phone: PhoneNumber
    purpose: ChallengePurpose = "login"


class OtpChallengeResponse(ApiModel):
    """Deliberately free of any signal about the account behind ``phone``.

    ``devCode`` is populated only outside production, where no SMS provider is
    wired up; in production the field is absent and the code exists only in the SMS.
    """

    challenge_id: UUID
    expires_at: datetime
    dev_code: str | None = None


class OtpVerifyRequest(ApiModel):
    challenge_id: UUID
    code: OtpCode
    display_name: Annotated[str, Field(min_length=2, max_length=160)] | None = None
    device: DeviceClaim | None = None


class PinLoginRequest(ApiModel):
    """Staff login. ``organizationId`` is required because a phone may hold
    memberships in several tenants and the PIN must be checked against a chosen one."""

    phone: PhoneNumber
    pin: Pin
    organization_id: UUID
    store_id: UUID | None = None
    device: DeviceClaim | None = None


class RefreshRequest(ApiModel):
    refresh_token: Annotated[str, Field(min_length=16, max_length=512)]


class SelectContextRequest(ApiModel):
    organization_id: UUID
    store_id: UUID | None = None
    device: DeviceClaim | None = None


class MembershipStore(ApiModel):
    """One branch inside a membership, named well enough to put on a button.

    The ids alone were not enough. A client offered ``["a3f...", "7c1..."]`` cannot
    ask a cashier which branch they are standing in, so all three shells silently
    took the first entry -- and since the query behind it had no ``ORDER BY``, that
    entry was whichever row the database happened to return. Sales then booked
    against a branch nobody chose, drawing down its stock.
    """

    id: UUID
    code: str
    name: str


class MembershipOption(ApiModel):
    """One tenant the authenticated user may select after OTP verification."""

    organization_id: UUID
    organization_name: str
    role: Role
    stores: list[MembershipStore]


class SessionResponse(ApiModel):
    id: UUID
    organization_id: UUID | None
    store_id: UUID | None
    device_id: UUID | None
    expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None
    current: bool = False


class TokenResponse(ApiModel):
    """The credential pair plus everything the client needs to render a shell.

    ``organizations`` is populated so a multi-tenant user can pick a context without
    a second round trip, and is the only place membership is disclosed.
    """

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    session_id: UUID
    user: UserResponse
    organization_id: UUID | None = None
    store_id: UUID | None = None
    role: Role | None = None
    requires_organization: bool = False
    organizations: list[MembershipOption] = Field(default_factory=list)

    @field_validator("access_token", "refresh_token")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("token must not be empty")
        return value


class CurrentUserResponse(ApiModel):
    """``GET /auth/me``: the caller's own identity as the live rows describe it.

    Role comes from the ``organization_users`` row rather than the token, so a
    demotion takes effect on the next request instead of at token expiry.
    """

    user: UserResponse
    organization_id: UUID
    organization_name: str
    role: Role
    store_id: UUID | None
    store_name: str | None
    device_id: UUID | None
    pin_set: bool
    session_id: UUID
    session_expires_at: datetime


class DeviceRegisterRequest(ApiModel):
    device_key: DeviceKey
    name: DeviceName


class DeviceResponse(ApiModel):
    id: UUID
    organization_id: UUID
    store_id: UUID
    device_key: str
    name: str
    status: DeviceStatus
    last_seen_at: datetime | None
    created_at: datetime


class LogoutResponse(ApiModel):
    revoked_session_ids: list[UUID]
