"""Route modules. Each feature owns exactly one file here and is mounted in ``app.main``."""

from app.routers.ai import router as ai_router
from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.billing import router as billing_router
from app.routers.cash import router as cash_router
from app.routers.catalog import router as catalog_router
from app.routers.customers import router as customers_router
from app.routers.ecommerce import router as ecommerce_router
from app.routers.inventory import router as inventory_router
from app.routers.loyalty import router as loyalty_router
from app.routers.orders import router as orders_router
from app.routers.organizations import router as organizations_router
from app.routers.payments import router as payments_router
from app.routers.prescriptions import router as prescriptions_router
from app.routers.products import router as products_router
from app.routers.purchase_orders import router as purchase_orders_router
from app.routers.purchasing import router as purchasing_router
from app.routers.reports import router as reports_router
from app.routers.sales import router as sales_router
from app.routers.storefront import router as storefront_router
from app.routers.stores import router as stores_router
from app.routers.supplier_network import router as supplier_network_router
from app.routers.suppliers import router as suppliers_router
from app.routers.sync import router as sync_router
from app.routers.users import router as users_router

__all__ = [
    "ai_router",
    "audit_router",
    "auth_router",
    "billing_router",
    "cash_router",
    "prescriptions_router",
    "orders_router",
    "ecommerce_router",
    "storefront_router",
    "catalog_router",
    "customers_router",
    "inventory_router",
    "loyalty_router",
    "organizations_router",
    "payments_router",
    "products_router",
    "purchase_orders_router",
    "purchasing_router",
    "sales_router",
    "reports_router",
    "stores_router",
    "supplier_network_router",
    "suppliers_router",
    "sync_router",
    "users_router",
]
