from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.customers import router as customers_router
from app.api.routes.orders import router as orders_router
from app.api.routes.payments import router as payments_router
from app.api.routes.products import router as products_router
from app.api.routes.refunds import payments_refunds_router, refunds_router
from app.api.routes.statistics import router as statistics_router
from app.api.routes.users import router as users_router
from app.api.routes.webhooks import admin_router as webhook_events_router
from app.api.routes.webhooks import receiver_router as webhooks_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(customers_router)
api_router.include_router(products_router)
api_router.include_router(orders_router)
api_router.include_router(payments_router)
api_router.include_router(payments_refunds_router)
api_router.include_router(refunds_router)
api_router.include_router(statistics_router)
api_router.include_router(webhooks_router)
api_router.include_router(webhook_events_router)
