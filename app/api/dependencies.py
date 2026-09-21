from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal, get_db
from app.integrations.stripe import StripeGateway, StripePaymentIntentGateway
from app.integrations.stripe_refund import (
    StripeRefundGateway,
    StripeRefundGatewayAdapter,
)
from app.integrations.stripe_webhook import StripeWebhookVerifier
from app.models.user import User, UserRole
from app.repositories.customer import CustomerRepository
from app.repositories.order import OrderRepository
from app.repositories.payment import PaymentRepository
from app.repositories.product import ProductRepository
from app.repositories.refund import RefundRepository
from app.repositories.statistics import StatisticsRepository
from app.repositories.stripe_payment import StripePaymentRepository
from app.repositories.stripe_webhook import StripeWebhookRepository
from app.repositories.user import UserRepository
from app.repositories.webhook_event import WebhookEventRepository
from app.services.auth import AuthService, InvalidTokenError
from app.services.customer import CustomerService
from app.services.health import ReadinessService
from app.services.order import OrderService
from app.services.payment import PaymentService
from app.services.product import ProductService
from app.services.refund import RefundService
from app.services.statistics import StatisticsService
from app.services.stripe_payment import StripePaymentService
from app.services.stripe_webhook import StripeWebhookService
from app.services.user import UserService
from app.services.webhook_event import WebhookEventService
from app.tasks import send_order_confirmation, send_payment_notification

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_auth_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthService:
    return AuthService(UserRepository(session), settings)


def get_user_service(
    session: Annotated[Session, Depends(get_db)],
) -> UserService:
    return UserService(UserRepository(session))


def get_readiness_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadinessService:
    return ReadinessService(SessionLocal, settings)


def get_statistics_service(
    session: Annotated[Session, Depends(get_db)],
) -> StatisticsService:
    return StatisticsService(StatisticsRepository(session))


def get_customer_service(
    session: Annotated[Session, Depends(get_db)],
) -> CustomerService:
    return CustomerService(CustomerRepository(session))


def get_product_service(
    session: Annotated[Session, Depends(get_db)],
) -> ProductService:
    return ProductService(ProductRepository(session))


def get_order_service(
    session: Annotated[Session, Depends(get_db)],
) -> OrderService:
    return OrderService(
        OrderRepository(session),
        confirmation_publisher=send_order_confirmation.delay,
    )


def get_payment_service(
    session: Annotated[Session, Depends(get_db)],
) -> PaymentService:
    return PaymentService(
        PaymentRepository(session),
        notification_publisher=send_payment_notification.delay,
    )


def get_webhook_event_service(
    session: Annotated[Session, Depends(get_db)],
) -> WebhookEventService:
    return WebhookEventService(WebhookEventRepository(session))


def get_stripe_gateway(
    settings: Annotated[Settings, Depends(get_settings)],
) -> StripeGateway:
    return StripePaymentIntentGateway(settings.stripe_secret_key)


def get_stripe_payment_service(
    session: Annotated[Session, Depends(get_db)],
    gateway: Annotated[StripeGateway, Depends(get_stripe_gateway)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StripePaymentService:
    return StripePaymentService(
        StripePaymentRepository(session),
        gateway,
        currency=settings.stripe_currency,
    )


def get_stripe_refund_gateway(
    settings: Annotated[Settings, Depends(get_settings)],
) -> StripeRefundGateway:
    return StripeRefundGatewayAdapter(settings.stripe_secret_key)


def get_refund_service(
    session: Annotated[Session, Depends(get_db)],
    gateway: Annotated[StripeRefundGateway, Depends(get_stripe_refund_gateway)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RefundService:
    return RefundService(
        RefundRepository(session),
        gateway,
        currency=settings.stripe_currency,
    )


def get_stripe_webhook_verifier(
    settings: Annotated[Settings, Depends(get_settings)],
) -> StripeWebhookVerifier:
    return StripeWebhookVerifier(settings.stripe_webhook_secret)


def get_stripe_webhook_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StripeWebhookService:
    return StripeWebhookService(
        StripeWebhookRepository(session),
        currency=settings.stripe_currency,
    )


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> User:
    try:
        return service.get_user_from_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_roles(*allowed_roles: UserRole) -> Callable[..., User]:
    allowed = frozenset(allowed_roles)

    def authorize(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return authorize


require_viewer = require_roles(
    UserRole.VIEWER,
    UserRole.OPERATOR,
    UserRole.ADMIN,
)
require_operator = require_roles(UserRole.OPERATOR, UserRole.ADMIN)
require_admin = require_roles(UserRole.ADMIN)
