from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
import stripe
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.dependencies import get_current_user, get_stripe_payment_service
from app.integrations.stripe import (
    StripeAuthenticationError,
    StripeConfigurationError,
    StripeIdempotencyError,
    StripeInvalidRequestError,
    StripePaymentIntent,
    StripePaymentIntentGateway,
    StripeProviderError,
    StripeTemporaryError,
)
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.models.user import User
from app.repositories.payment import (
    DuplicatePaymentReferenceError,
    PaymentConstraintError,
)
from app.repositories.stripe_payment import OrderPaymentSnapshot
from app.schemas.stripe_payment import StripePaymentCreate
from app.services.stripe_payment import (
    StripePaymentAmountError,
    StripePaymentService,
    build_stripe_idempotency_key,
    decimal_to_minor_units,
)


class FakeStripeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.intents_by_key: dict[str, StripePaymentIntent] = {}
        self.error: Exception | None = None
        self.after_create: object | None = None

    def create_payment_intent(
        self,
        *,
        amount: int,
        currency: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> StripePaymentIntent:
        self.calls.append(
            {
                "amount": amount,
                "currency": currency,
                "metadata": metadata,
                "idempotency_key": idempotency_key,
            }
        )
        if self.error is not None:
            raise self.error
        intent = self.intents_by_key.get(idempotency_key)
        if intent is None:
            intent = StripePaymentIntent(
                id=f"pi_test_{len(self.intents_by_key) + 1}",
                status="requires_payment_method",
                amount=amount,
                currency=currency,
            )
            self.intents_by_key[idempotency_key] = intent
        if callable(self.after_create):
            self.after_create()
        return intent


class InMemoryStripePaymentRepository:
    def __init__(self) -> None:
        self.orders: dict[int, Order] = {}
        self.payments: dict[int, Payment] = {}
        self.fail_next_create = False
        self._next_id = 1

    def get_order_snapshot(self, order_id: int) -> OrderPaymentSnapshot | None:
        order = self.orders.get(order_id)
        if order is None:
            return None
        return OrderPaymentSnapshot(
            id=order.id,
            status=order.status,
            total_amount=order.total_amount,
        )

    def get_order_for_update(self, order_id: int) -> Order | None:
        return self.orders.get(order_id)

    def get_by_provider_reference(self, provider_reference: str) -> Payment | None:
        return next(
            (
                payment
                for payment in self.payments.values()
                if payment.provider_reference == provider_reference
            ),
            None,
        )

    def create(
        self,
        *,
        order_id: int,
        provider_reference: str,
        amount: Decimal,
    ) -> Payment:
        if self.fail_next_create:
            self.fail_next_create = False
            raise PaymentConstraintError
        if self.get_by_provider_reference(provider_reference) is not None:
            raise DuplicatePaymentReferenceError
        now = datetime.now(UTC)
        payment = Payment(
            id=self._next_id,
            order_id=order_id,
            provider="stripe",
            provider_reference=provider_reference,
            amount=amount,
            status=PaymentStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        self.payments[payment.id] = payment
        self._next_id += 1
        return payment


@pytest.fixture
def stripe_context() -> Generator[
    tuple[TestClient, InMemoryStripePaymentRepository, FakeStripeGateway]
]:
    repository = InMemoryStripePaymentRepository()
    gateway = FakeStripeGateway()
    now = datetime.now(UTC)
    for order_id, status, amount in [
        (1, OrderStatus.PENDING, Decimal("239.82")),
        (2, OrderStatus.CANCELLED, Decimal("19.90")),
        (3, OrderStatus.CONFIRMED, Decimal("0.01")),
    ]:
        repository.orders[order_id] = Order(
            id=order_id,
            customer_id=1,
            status=status,
            total_amount=amount,
            created_at=now,
            updated_at=now,
        )
    service = StripePaymentService(repository, gateway, currency="brl")  # type: ignore[arg-type]
    authenticated_user = User(
        id=1,
        full_name="Stripe User",
        email="stripe-user@example.com",
        hashed_password="not-used",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_stripe_payment_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: authenticated_user

    with TestClient(app) as client:
        yield client, repository, gateway

    app.dependency_overrides.pop(get_stripe_payment_service, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize(
    ("amount", "minor_units"),
    [
        ("0.01", 1),
        ("0.50", 50),
        ("19.90", 1990),
        ("199.99", 19999),
        ("239.82", 23982),
    ],
)
def test_decimal_to_minor_units_is_exact(amount: str, minor_units: int) -> None:
    assert decimal_to_minor_units(Decimal(amount)) == minor_units


@pytest.mark.parametrize("amount", ["0", "-0.01", "1.001"])
def test_decimal_to_minor_units_rejects_invalid_values(amount: str) -> None:
    with pytest.raises(StripePaymentAmountError):
        decimal_to_minor_units(Decimal(amount))


def test_stripe_endpoint_requires_bearer_token() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/payments/stripe",
            json={"order_id": 1},
            headers={"Idempotency-Key": "attempt-1"},
        )

    assert response.status_code == 401


def test_stripe_payment_uses_server_amount_currency_metadata_and_pending_status(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
) -> None:
    client, repository, gateway = stripe_context

    response = client.post(
        "/api/v1/payments/stripe",
        json={"order_id": 1},
        headers={"Idempotency-Key": "attempt-1"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["payment"]["provider"] == "stripe"
    assert body["payment"]["provider_reference"] == "pi_test_1"
    assert body["payment"]["amount"] == "239.82"
    assert body["payment"]["status"] == "pending"
    assert body["stripe"] == {
        "payment_intent_id": "pi_test_1",
        "status": "requires_payment_method",
        "amount": 23982,
        "currency": "brl",
    }
    assert "client_secret" not in str(body)
    assert gateway.calls == [
        {
            "amount": 23982,
            "currency": "brl",
            "metadata": {"orderflow_order_id": "1"},
            "idempotency_key": build_stripe_idempotency_key(1, "attempt-1"),
        }
    ]
    persisted = repository.payments[body["payment"]["id"]]
    assert persisted.amount == Decimal("239.82")
    assert persisted.status == PaymentStatus.PENDING


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"order_id": 0},
        {"order_id": 1, "amount": "0.50"},
        {"order_id": 1, "currency": "usd"},
        {"order_id": 1, "provider": "stripe"},
        {"order_id": 1, "provider_reference": "pi_client"},
        {"order_id": 1, "status": "approved"},
    ],
)
def test_stripe_endpoint_rejects_invalid_or_server_controlled_payload(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
    payload: dict[str, object],
) -> None:
    client, repository, gateway = stripe_context

    response = client.post(
        "/api/v1/payments/stripe",
        json=payload,
        headers={"Idempotency-Key": "attempt-invalid"},
    )

    assert response.status_code == 422
    assert repository.payments == {}
    assert gateway.calls == []


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": "   "}])
def test_stripe_endpoint_requires_valid_idempotency_header(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
    headers: dict[str, str],
) -> None:
    client, repository, gateway = stripe_context

    response = client.post(
        "/api/v1/payments/stripe",
        json={"order_id": 1},
        headers=headers,
    )

    assert response.status_code == 422
    assert repository.payments == {}
    assert gateway.calls == []


def test_missing_and_cancelled_orders_never_call_stripe(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
) -> None:
    client, repository, gateway = stripe_context

    missing = client.post(
        "/api/v1/payments/stripe",
        json={"order_id": 999},
        headers={"Idempotency-Key": "missing"},
    )
    cancelled = client.post(
        "/api/v1/payments/stripe",
        json={"order_id": 2},
        headers={"Idempotency-Key": "cancelled"},
    )

    assert missing.status_code == 404
    assert cancelled.status_code == 409
    assert repository.payments == {}
    assert gateway.calls == []


def test_amount_below_stripe_brl_minimum_is_rejected_before_external_call(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
) -> None:
    client, repository, gateway = stripe_context

    response = client.post(
        "/api/v1/payments/stripe",
        json={"order_id": 3},
        headers={"Idempotency-Key": "too-small"},
    )

    assert response.status_code == 422
    assert repository.payments == {}
    assert gateway.calls == []


def test_same_operation_key_recovers_same_intent_and_local_payment(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
) -> None:
    client, repository, gateway = stripe_context
    headers = {"Idempotency-Key": "stable-attempt"}

    first = client.post(
        "/api/v1/payments/stripe", json={"order_id": 1}, headers=headers
    )
    second = client.post(
        "/api/v1/payments/stripe", json={"order_id": 1}, headers=headers
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(repository.payments) == 1
    assert len(gateway.intents_by_key) == 1
    assert gateway.calls[0]["idempotency_key"] == gateway.calls[1]["idempotency_key"]


def test_different_operation_keys_allow_multiple_stripe_attempts_per_order(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
) -> None:
    client, repository, gateway = stripe_context

    first = client.post(
        "/api/v1/payments/stripe",
        json={"order_id": 1},
        headers={"Idempotency-Key": "attempt-a"},
    )
    second = client.post(
        "/api/v1/payments/stripe",
        json={"order_id": 1},
        headers={"Idempotency-Key": "attempt-b"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert len(repository.payments) == 2
    assert len(gateway.intents_by_key) == 2


def test_local_failure_after_stripe_is_recovered_with_same_operation_key(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
) -> None:
    client, repository, gateway = stripe_context
    repository.fail_next_create = True
    headers = {"Idempotency-Key": "recovery-attempt"}

    failed = client.post(
        "/api/v1/payments/stripe", json={"order_id": 1}, headers=headers
    )
    recovered = client.post(
        "/api/v1/payments/stripe", json={"order_id": 1}, headers=headers
    )

    assert failed.status_code == 409
    assert recovered.status_code == 201
    assert len(gateway.intents_by_key) == 1
    assert len(repository.payments) == 1
    assert recovered.json()["payment"]["provider_reference"] == "pi_test_1"


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (StripeConfigurationError(), 503),
        (StripeAuthenticationError(), 502),
        (StripeInvalidRequestError(), 502),
        (StripeIdempotencyError(), 409),
        (StripeTemporaryError(), 503),
        (StripeProviderError(), 502),
    ],
)
def test_stripe_errors_are_sanitized_and_leave_no_local_payment(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
    error: Exception,
    status_code: int,
) -> None:
    client, repository, gateway = stripe_context
    gateway.error = error

    response = client.post(
        "/api/v1/payments/stripe",
        json={"order_id": 1},
        headers={"Idempotency-Key": "external-error"},
    )

    assert response.status_code == status_code
    assert "secret" not in response.text.lower()
    assert repository.payments == {}


def test_order_change_after_stripe_prevents_inconsistent_local_payment(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
) -> None:
    client, repository, gateway = stripe_context
    gateway.after_create = lambda: setattr(
        repository.orders[1], "total_amount", Decimal("199.99")
    )

    response = client.post(
        "/api/v1/payments/stripe",
        json={"order_id": 1},
        headers={"Idempotency-Key": "changed-order"},
    )

    assert response.status_code == 409
    assert len(gateway.intents_by_key) == 1
    assert repository.payments == {}


def test_official_adapter_sends_current_payment_intent_contract() -> None:
    captured: dict[str, object] = {}

    class FakePaymentIntents:
        def create(
            self,
            params: dict[str, object],
            options: dict[str, str],
        ) -> SimpleNamespace:
            captured["params"] = params
            captured["options"] = options
            return SimpleNamespace(
                id="pi_test_adapter",
                status="requires_payment_method",
                amount=1990,
                currency="brl",
                client_secret="must-not-leave-adapter",
            )

    fake_client = SimpleNamespace(
        v1=SimpleNamespace(payment_intents=FakePaymentIntents())
    )
    gateway = StripePaymentIntentGateway(
        SecretStr("sk_test_placeholder"),
        client=fake_client,  # type: ignore[arg-type]
    )

    result = gateway.create_payment_intent(
        amount=1990,
        currency="brl",
        metadata={"orderflow_order_id": "10"},
        idempotency_key="stable-key",
    )

    assert captured == {
        "params": {
            "amount": 1990,
            "currency": "brl",
            "automatic_payment_methods": {"enabled": True},
            "metadata": {"orderflow_order_id": "10"},
        },
        "options": {"idempotency_key": "stable-key"},
    }
    assert result == StripePaymentIntent(
        id="pi_test_adapter",
        status="requires_payment_method",
        amount=1990,
        currency="brl",
    )
    assert not hasattr(result, "client_secret")


@pytest.mark.parametrize(
    ("sdk_error", "expected_error"),
    [
        (
            stripe.AuthenticationError("authentication failed"),
            StripeAuthenticationError,
        ),
        (stripe.PermissionError("permission failed"), StripeAuthenticationError),
        (
            stripe.InvalidRequestError("invalid request", param="amount"),
            StripeInvalidRequestError,
        ),
        (stripe.IdempotencyError("key conflict"), StripeIdempotencyError),
        (stripe.APIConnectionError("network failed"), StripeTemporaryError),
        (stripe.APIError("provider failed"), StripeTemporaryError),
        (stripe.RateLimitError("rate limited"), StripeTemporaryError),
    ],
)
def test_official_adapter_maps_current_sdk_errors(
    sdk_error: stripe.StripeError,
    expected_error: type[Exception],
) -> None:
    class FailingPaymentIntents:
        @staticmethod
        def create(
            params: dict[str, object],
            options: dict[str, str],
        ) -> None:
            raise sdk_error

    fake_client = SimpleNamespace(
        v1=SimpleNamespace(payment_intents=FailingPaymentIntents())
    )
    gateway = StripePaymentIntentGateway(
        SecretStr("sk_test_placeholder"),
        client=fake_client,  # type: ignore[arg-type]
    )

    with pytest.raises(expected_error):
        gateway.create_payment_intent(
            amount=1990,
            currency="brl",
            metadata={"orderflow_order_id": "10"},
            idempotency_key="stable-key",
        )


@pytest.mark.parametrize(
    "secret_key",
    [None, SecretStr(""), SecretStr("sk_live_not-allowed")],
)
def test_official_adapter_rejects_missing_or_live_keys(
    secret_key: SecretStr | None,
) -> None:
    gateway = StripePaymentIntentGateway(secret_key)

    with pytest.raises(StripeConfigurationError):
        gateway.create_payment_intent(
            amount=1990,
            currency="brl",
            metadata={"orderflow_order_id": "1"},
            idempotency_key="test-key",
        )


def test_stripe_openapi_documents_authenticated_safe_contract() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/payments/stripe"]["post"]

    assert operation["security"] == [{"OAuth2PasswordBearer": []}]
    assert {"200", "201", "401", "404", "409", "422", "502", "503"} <= set(
        operation["responses"]
    )
    assert {
        "StripePaymentCreate",
        "StripePaymentIntentResponse",
        "StripePaymentResponse",
    } <= set(schema["components"]["schemas"])
    serialized = str(operation).lower()
    assert "stripe_secret_key" not in serialized
    assert "client_secret" not in serialized


def test_service_can_be_called_directly_with_only_order_and_operation_key(
    stripe_context: tuple[
        TestClient, InMemoryStripePaymentRepository, FakeStripeGateway
    ],
) -> None:
    _, repository, gateway = stripe_context
    service = StripePaymentService(repository, gateway, currency="brl")  # type: ignore[arg-type]

    receipt = service.create(
        StripePaymentCreate(order_id=1),
        operation_key="direct-attempt",
    )

    assert receipt.payment.provider == "stripe"
