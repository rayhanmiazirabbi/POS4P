from __future__ import annotations

from datetime import datetime
from typing import Annotated, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class ApiModel(BaseModel):
    """Base for every request/response body.

    Serializes camelCase to match the shared TypeScript contracts in
    ``@pharmacy/types`` while keeping snake_case attribute names in Python.
    ``extra="forbid"`` enforces the unknown-field policy the specs require.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        extra="forbid",
        use_enum_values=True,
        ser_json_timedelta="iso8601",
    )


class Envelope[T](BaseModel):
    """Mirrors ``ApiResponse<T>``: every success payload carries its request id."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    data: T
    request_id: str


class PageMeta(ApiModel):
    next_cursor: str | None = None
    total: int | None = None


class Page[T](BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


class PaginationParams(ApiModel):
    cursor: str | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 25


class TimestampedModel(ApiModel):
    id: UUID
    created_at: datetime
