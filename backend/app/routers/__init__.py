"""Route modules. Each feature owns exactly one file here and is mounted in ``app.main``."""

from app.routers.auth import router as auth_router
from app.routers.organizations import router as organizations_router
from app.routers.stores import router as stores_router
from app.routers.users import router as users_router

__all__ = ["auth_router", "organizations_router", "stores_router", "users_router"]
