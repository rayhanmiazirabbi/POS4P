from __future__ import annotations

import re

from app.main import app

#: The shared TypeScript clients build every query string in camelCase (see
#: ``@pharmacy/api``'s ``buildQuery``), and ``ApiModel`` already serializes bodies
#: that way. A snake_case query parameter is therefore unreachable from the real
#: clients -- FastAPI silently falls back to the default instead of erroring, so
#: the filter looks like it works until someone checks the rows it returned.
_SNAKE = re.compile(r"_[a-z]")

#: Reserved words and deliberate shortenings, listed so that adding one is a
#: decision rather than drift.
INTENTIONAL_NAMES = frozenset({"from", "to", "status", "q"})


def _to_camel(name: str) -> str:
    return _SNAKE.sub(lambda match: match.group(0)[1].upper(), name)


def _query_parameters() -> list[tuple[str, str]]:
    """Every (route, wire name) query parameter, read off the published schema.

    The OpenAPI document is the contract the TypeScript clients are written
    against, so asserting on it checks the thing that actually ships.
    """
    schema = app.openapi()
    found: list[tuple[str, str]] = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                if parameter.get("in") != "query":
                    continue
                found.append((f"{method.upper()} {path}", parameter["name"]))
    return found


def test_the_app_actually_exposes_query_parameters() -> None:
    """Guard the guard: a broken schema walk would make the check below vacuous."""
    assert len(_query_parameters()) > 20


def test_every_query_parameter_is_camel_case_on_the_wire() -> None:
    """Locks the query-string contract across every router."""
    offenders = sorted(
        f"{label}: '{name}' should be '{_to_camel(name)}'"
        for label, name in _query_parameters()
        if "_" in name and name not in INTENTIONAL_NAMES
    )
    assert offenders == []
