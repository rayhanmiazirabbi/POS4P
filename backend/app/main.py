from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request

from app.errors import register_exception_handlers
from app.routers import (
    auth_router,
    catalog_router,
    inventory_router,
    organizations_router,
    products_router,
    purchasing_router,
    stores_router,
    suppliers_router,
    users_router,
)

app = FastAPI(title="Pharmacy Platform API", version="0.1.0")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


register_exception_handlers(app)

for router in (
    auth_router,
    organizations_router,
    stores_router,
    users_router,
    catalog_router,
    products_router,
    suppliers_router,
    inventory_router,
    purchasing_router,
):
    app.include_router(router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
