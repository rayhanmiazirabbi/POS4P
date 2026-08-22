"""Route modules. Each feature owns exactly one file here and is mounted in ``app.main``."""

from app.routers.auth import router as auth_router
from app.routers.catalog import router as catalog_router
from app.routers.inventory import router as inventory_router
from app.routers.organizations import router as organizations_router
from app.routers.products import router as products_router
from app.routers.purchasing import router as purchasing_router
from app.routers.stores import router as stores_router
from app.routers.suppliers import router as suppliers_router
from app.routers.users import router as users_router

__all__ = [
    "auth_router",
    "catalog_router",
    "inventory_router",
    "organizations_router",
    "products_router",
    "purchasing_router",
    "stores_router",
    "suppliers_router",
    "users_router",
]
