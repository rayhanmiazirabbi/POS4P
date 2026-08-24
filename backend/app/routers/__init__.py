"""Route modules. Each feature owns exactly one file here and is mounted in ``app.main``."""

from app.routers.auth import router as auth_router
from app.routers.catalog import router as catalog_router
from app.routers.customers import router as customers_router
from app.routers.inventory import router as inventory_router
from app.routers.loyalty import router as loyalty_router
from app.routers.organizations import router as organizations_router
from app.routers.payments import router as payments_router
from app.routers.products import router as products_router
from app.routers.purchasing import router as purchasing_router
from app.routers.reports import router as reports_router
from app.routers.sales import router as sales_router
from app.routers.stores import router as stores_router
from app.routers.suppliers import router as suppliers_router
from app.routers.sync import router as sync_router
from app.routers.users import router as users_router

__all__ = [
    "auth_router",
    "catalog_router",
    "customers_router",
    "inventory_router",
    "loyalty_router",
    "organizations_router",
    "payments_router",
    "products_router",
    "purchasing_router",
    "sales_router",
    "reports_router",
    "stores_router",
    "suppliers_router",
    "sync_router",
    "users_router",
]
