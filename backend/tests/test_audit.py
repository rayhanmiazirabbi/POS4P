from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit import make_audit_log
from app.models import AuditLog, Role
from tests._phase4_helpers import role_headers
from tests.conftest import access_token_for


def _headers(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed(session: AsyncSession, tenant: dict[str, Any]) -> list[AuditLog]:
    rows = [
        make_audit_log(
            tenant["organization"].id,
            action=f"sale.{suffix}",
            entity_type="sale",
            request_id=str(uuid4()),
            store_id=tenant["store"].id,
        )
        for suffix in ("created", "voided", "returned")
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def test_owner_can_search_by_action_prefix_and_q(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    await _seed(session, tenant)

    by_prefix = await client.get("/audit/logs", params={"action": "sale."}, headers=_headers(tenant))
    assert by_prefix.status_code == 200, by_prefix.text
    actions = {row["action"] for row in by_prefix.json()["data"]["items"]}
    assert actions == {"sale.created", "sale.voided", "sale.returned"}

    by_q = await client.get("/audit/logs", params={"q": "void"}, headers=_headers(tenant))
    items = by_q.json()["data"]["items"]
    assert [row["action"] for row in items] == ["sale.voided"]

    narrowed = await client.get(
        "/audit/logs", params={"action": "sale.", "entityType": "nope"}, headers=_headers(tenant)
    )
    assert narrowed.json()["data"]["items"] == []


async def test_search_is_scoped_to_the_callers_tenant(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_organization: Any,
    make_user: Any,
) -> None:
    stranger_org = await make_organization(slug="stranger-org")
    session.add(
        make_audit_log(stranger_org.id, action="sale.created", entity_type="sale", request_id="s1")
    )
    await _seed(session, tenant)

    response = await client.get("/audit/logs", headers=_headers(tenant))
    assert response.json()["data"]["total"] == 3  # the stranger's row is invisible
    request_ids = {row["requestId"] for row in response.json()["data"]["items"]}
    assert "s1" not in request_ids


async def test_audit_view_is_owner_only_and_date_filtered(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    await _seed(session, tenant)

    manager = await client.get(
        "/audit/logs", headers=await role_headers(session, tenant, Role.MANAGER)
    )
    assert manager.status_code == 403

    empty_window = await client.get(
        "/audit/logs",
        params={
            "dateFrom": (datetime.now(UTC) - timedelta(days=7)).isoformat(),
            "dateTo": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
        headers=_headers(tenant),
    )
    assert empty_window.status_code == 200
    assert empty_window.json()["data"]["total"] == 0

    covering_window = await client.get(
        "/audit/logs",
        params={"dateFrom": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
        headers=_headers(tenant),
    )
    assert covering_window.json()["data"]["total"] == 3


async def test_export_returns_csv_of_filtered_rows(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    await _seed(session, tenant)

    everything = await client.post("/audit/logs/export", json={}, headers=_headers(tenant))
    assert everything.status_code == 200, everything.text
    assert everything.headers["content-type"].startswith("text/csv")
    lines = everything.text.strip().splitlines()
    assert lines[0].startswith("id,created_at,action")
    assert len(lines) == 4  # header + three seeded rows

    filtered = await client.post(
        "/audit/logs/export", json={"action": "sale.voided"}, headers=_headers(tenant)
    )
    body = filtered.text.strip().splitlines()
    assert len(body) == 2
    assert ",sale.voided," in body[1]


async def test_export_never_contains_payload_summaries(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    session.add(
        AuditLog(
            organization_id=tenant["organization"].id,
            actor_user_id=tenant["owner"].id,
            action="customer.updated",
            entity_type="customer",
            request_id=str(uuid4()),
            before_data={"name": "Old Name"},
            after_data={"name": "New Name"},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()

    exported = await client.post("/audit/logs/export", json={}, headers=_headers(tenant))
    # The CSV carries who/what/when only; payload summaries stay behind the API.
    assert "New Name" not in exported.text
    assert "before_data" not in exported.text


async def test_new_entries_are_signed_and_verify_clean(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    from app.context import RequestContext
    from app.services.audit import record_audit

    context = RequestContext(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        user_id=tenant["owner"].id,
        role=Role.OWNER,
    )
    record_audit(
        session,
        context,
        action="test.signed",
        entity_type="widget",
        request_id="signed-1",
        after={"note": "hello"},
    )
    await session.commit()

    clean = await client.get("/audit/logs/verify", headers=_headers(tenant))
    assert clean.status_code == 200, clean.text
    assert clean.json()["data"]["tampered"] == []


async def test_tampered_row_is_detected_by_verification(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    from sqlalchemy import update

    from app.context import RequestContext
    from app.services.audit import record_audit

    context = RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=Role.OWNER,
    )
    record_audit(
        session,
        context,
        action="test.tamperme",
        entity_type="widget",
        request_id="tamper-1",
    )
    await session.commit()

    # Rewrite history behind the ORM's back, the way an intruder with database
    # access would: a raw UPDATE bypasses the append-only guard.
    await session.execute(
        update(AuditLog)
        .where(AuditLog.request_id == "tamper-1")
        .values(action="test.innocent")
    )
    await session.commit()
    await session.expire_all()

    report = await client.get("/audit/logs/verify", headers=_headers(tenant))
    tampered = report.json()["data"]["tampered"]
    assert len(tampered) == 1

    manager = await client.get(
        "/audit/logs/verify", headers=await role_headers(session, tenant, Role.MANAGER)
    )
    assert manager.status_code == 403


async def test_prune_removes_only_rows_past_retention(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], monkeypatch: Any
) -> None:
    from app.config import get_settings

    old_row = make_audit_log(
        tenant["organization"].id,
        action="sale.created",
        entity_type="sale",
        request_id="ancient",
    )
    old_row.created_at = datetime.now(UTC) - timedelta(days=400)
    fresh_row = make_audit_log(
        tenant["organization"].id,
        action="sale.created",
        entity_type="sale",
        request_id="fresh",
    )
    session.add_all([old_row, fresh_row])
    await session.commit()

    monkeypatch.setattr(get_settings(), "audit_retention_days", 365)
    response = await client.post("/audit/logs/prune", headers=_headers(tenant))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["pruned"] == 1

    remaining = await client.get("/audit/logs", headers=_headers(tenant))
    request_ids = {row["requestId"] for row in remaining.json()["data"]["items"]}
    assert "ancient" not in request_ids
    assert "fresh" in request_ids
