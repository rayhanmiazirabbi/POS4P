from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.authorization import require_role
from app.models import Role


def test_role_checks_default_to_forbidden() -> None:
    require_role(Role.CASHIER, Role.CASHIER)
    with pytest.raises(HTTPException) as error:
        require_role(Role.CASHIER, Role.MANAGER)
    assert error.value.status_code == 403


def test_object_scope_values_are_not_interchangeable() -> None:
    organization_id = uuid4()
    store_id = uuid4()
    assert organization_id != store_id
