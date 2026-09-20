import json
import time
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import stripe
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.dependencies import (
    get_stripe_webhook_service,
    get_stripe_webhook_verifier,
)
from app.core.config import Settings
from app.integrations.stripe_webhook import StripeWebhookVerifier
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.models.product import Product
from app.models.webhook_event import WebhookEvent
from app.repositories.webhook_event import WebhookEventConstraintError
from app.services.stripe_webhook import StripeWebhookService

WEBHOOK_SECRET = "whsec_phase13_test_only"


class InMemoryStripeWebhookRepository:
    def __init__(self) -> None:
        self.events: dict[str, WebhookEvent] = {}
        self.payments: dict[str, Payment] = {}
        self.process_calls = 0
        self.fail_next_create = False
        self._next_id = 1

    def get_by_provider_event_id(self, provider_event_id: str) -> WebhookEvent | None:
        return self.events.get(provider_event_id)

    def create_received(
        self,
        *,
        provider: str,
        provider_event_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> WebhookEvent:
        if self.fail_next_create:
            self.fail_next_create = False
            raise WebhookEventConstraintError
        event = WebhookEvent(
            id=self._next_id,
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=payload,
            received_at=datetime.now(UTC),
            processed_at=None,
        )
        self._next_id += 1
        return event

    def get_payment_for_update(self, provider_reference: str) -> Payment | None:
        return self.payments.get(provider_reference)

    def commit_received(self, event: WebhookEvent) -> WebhookEvent:
        self.events[event.provider_event_id] = event
        return event

    def commit_processed(
        self,
        event: WebhookEvent,
        payment: Payment,
        status: PaymentStatus,
    ) -> WebhookEvent:
        self.process_calls += 1
        payment.status = status
        event.processed_at = datetime.now(UTC)
        self.events[event.provider_event_id] = event
        return event


def stripe_event(
    event_id: str,
    event_type: str,
    *,
    intent_id: str = "pi_phase13",
    amount: int = 23982,
    currency: str = "brl",
) -> dict[str, object]:
    return {
        "id": event_id,
        "object": "event",
        "api_version": "2025-04-30.basil",
        "created": 1_700_000_000,
        "livemode": False,
        "type": event_type,
        "data": {
            "object": {
                "id": intent_id,
                "object": "payment_intent",
                "amount": amount,
                "currency": currency,
            }
        },
    }


def encode_event(event: dict[str, object], *, indent: int | None = None) -> bytes:
    return json.dumps(
        event, separators=None if indent else (",", ":"), indent=indent
    ).encode()


def signed_headers(raw_body: bytes, secret: str = WEBHOOK_SECRET) -> dict[str, str]:
    signature = stripe.WebhookSignature.generate_signature_header(
        raw_body.decode(),
        secret,
        timestamp=int(time.time()),
    )
    return {
        "Content-Type": "application/json",
        "Stripe-Signature": signature,
    }


@pytest.fixture
def stripe_webhook_context() -> Generator[
    tuple[
        TestClient,
        InMemoryStripeWebhookRepository,
        Payment,
        Order,
        Product,
    ]
]:
    repository = InMemoryStripeWebhookRepository()
    now = datetime.now(UTC)
    order = Order(
        id=1,
        customer_id=1,
        status=OrderStatus.PENDING,
        total_amount=Decimal("239.82"),
        created_at=now,
        updated_at=now,
    )
    product = Product(
        id=1,
        sku="STRIPE-WEBHOOK",
        name="Stripe Webhook Product",
        price=Decimal("239.82"),
        stock=7,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    payment = Payment(
        id=1,
        order_id=order.id,
        provider="stripe",
        provider_reference="pi_phase13",
        amount=Decimal("239.82"),
        status=PaymentStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    repository.payments["pi_phase13"] = payment
    service = StripeWebhookService(repository, currency="brl")  # type: ignore[arg-type]
    verifier = StripeWebhookVerifier(SecretStr(WEBHOOK_SECRET))
    app.dependency_overrides[get_stripe_webhook_service] = lambda: service
    app.dependency_overrides[get_stripe_webhook_verifier] = lambda: verifier
    with TestClient(app) as client:
        yield client, repository, payment, order, product
    app.dependency_overrides.pop(get_stripe_webhook_service, None)
    app.dependency_overrides.pop(get_stripe_webhook_verifier, None)


def post_signed(
    client: TestClient,
    event: dict[str, object],
    *,
    raw_body: bytes | None = None,
    secret: str = WEBHOOK_SECRET,
):
    body = raw_body if raw_body is not None else encode_event(event)
    return client.post(
        "/api/v1/webhooks/stripe",
        content=body,
        headers=signed_headers(body, secret),
    )


def test_valid_signature_uses_raw_body_and_approves_payment(
    stripe_webhook_context: tuple,
) -> None:
    client, repository, payment, order, product = stripe_webhook_context
    raw_body = encode_event(
        stripe_event("evt_succeeded", "payment_intent.succeeded"),
        indent=2,
    )

    response = client.post(
        "/api/v1/webhooks/stripe",
        content=raw_body,
        headers=signed_headers(raw_body),
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    assert payment.status == PaymentStatus.APPROVED
    event = repository.events["evt_succeeded"]
    assert event.provider == "stripe"
    assert event.event_type == "payment_intent.succeeded"
    assert event.payload["id"] == "evt_succeeded"
    assert event.processed_at is not None
    assert payment.amount == Decimal("239.82")
    assert order.status == OrderStatus.PENDING
    assert product.stock == 7


def test_payment_failed_event_uses_existing_transition_machine(
    stripe_webhook_context: tuple,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context

    response = post_signed(
        client,
        stripe_event("evt_failed", "payment_intent.payment_failed"),
    )

    assert response.status_code == 200
    assert payment.status == PaymentStatus.FAILED
    assert repository.events["evt_failed"].processed_at is not None


@pytest.mark.parametrize("case", ["invalid-signature", "changed-body", "wrong-secret"])
def test_invalid_authenticity_never_persists_or_changes_payment(
    stripe_webhook_context: tuple,
    case: str,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context
    event = stripe_event("evt_untrusted", "payment_intent.succeeded")
    signed_body = encode_event(event)
    headers = signed_headers(
        signed_body,
        "whsec_different_test_secret" if case == "wrong-secret" else WEBHOOK_SECRET,
    )
    if case == "invalid-signature":
        headers["Stripe-Signature"] = "t=1,v1=invalid"
    sent_body = signed_body + b" " if case == "changed-body" else signed_body

    response = client.post(
        "/api/v1/webhooks/stripe",
        content=sent_body,
        headers=headers,
    )

    assert response.status_code == 400
    assert repository.events == {}
    assert payment.status == PaymentStatus.PENDING


def test_missing_signature_header_is_rejected_before_persistence(
    stripe_webhook_context: tuple,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context

    response = client.post(
        "/api/v1/webhooks/stripe",
        content=encode_event(stripe_event("evt_no_header", "payment_intent.succeeded")),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert repository.events == {}
    assert payment.status == PaymentStatus.PENDING


def test_missing_webhook_secret_returns_503(
    stripe_webhook_context: tuple,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context
    app.dependency_overrides[get_stripe_webhook_verifier] = lambda: (
        StripeWebhookVerifier(None)
    )
    body = encode_event(stripe_event("evt_no_secret", "payment_intent.succeeded"))

    response = client.post(
        "/api/v1/webhooks/stripe",
        content=body,
        headers=signed_headers(body),
    )

    assert response.status_code == 503
    assert repository.events == {}
    assert payment.status == PaymentStatus.PENDING


def test_signed_invalid_json_returns_400_without_effect(
    stripe_webhook_context: tuple,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context
    body = b'{"id":"evt_broken"'

    response = client.post(
        "/api/v1/webhooks/stripe",
        content=body,
        headers=signed_headers(body),
    )

    assert response.status_code == 400
    assert repository.events == {}
    assert payment.status == PaymentStatus.PENDING


@pytest.mark.parametrize("invalid_shape", ["missing-data", "live-event"])
def test_signed_non_snapshot_or_live_event_is_rejected(
    stripe_webhook_context: tuple,
    invalid_shape: str,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context
    event = stripe_event("evt_invalid_shape", "payment_intent.succeeded")
    if invalid_shape == "missing-data":
        event.pop("data")
    else:
        event["livemode"] = True

    response = post_signed(client, event)

    assert response.status_code == 400
    assert repository.events == {}
    assert payment.status == PaymentStatus.PENDING


@pytest.mark.parametrize(
    ("event_id", "event_type"),
    [
        ("evt_unknown", "payment_intent.processing"),
        ("evt_charge_refunded", "charge.refunded"),
        ("evt_refund_created", "refund.created"),
    ],
)
def test_unhandled_and_refund_events_are_audited_without_domain_effect(
    stripe_webhook_context: tuple,
    event_id: str,
    event_type: str,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context

    response = post_signed(client, stripe_event(event_id, event_type))

    assert response.status_code == 200
    assert payment.status == PaymentStatus.PENDING
    assert repository.events[event_id].processed_at is None


@pytest.mark.parametrize(
    ("event_id", "intent_id", "amount", "currency"),
    [
        ("evt_missing", "pi_missing", 23982, "brl"),
        ("evt_amount_mismatch", "pi_phase13", 100, "brl"),
        ("evt_currency_mismatch", "pi_phase13", 23982, "usd"),
    ],
)
def test_non_actionable_authenticated_events_are_acknowledged_and_audited(
    stripe_webhook_context: tuple,
    event_id: str,
    intent_id: str,
    amount: int,
    currency: str,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context

    response = post_signed(
        client,
        stripe_event(
            event_id,
            "payment_intent.succeeded",
            intent_id=intent_id,
            amount=amount,
            currency=currency,
        ),
    )

    assert response.status_code == 200
    assert repository.events[event_id].processed_at is None
    assert payment.status == PaymentStatus.PENDING
    assert payment.amount == Decimal("239.82")


def test_payment_with_wrong_provider_is_not_changed(
    stripe_webhook_context: tuple,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context
    payment.provider = "manual"

    response = post_signed(
        client,
        stripe_event("evt_wrong_provider", "payment_intent.succeeded"),
    )

    assert response.status_code == 200
    assert payment.status == PaymentStatus.PENDING
    assert repository.events["evt_wrong_provider"].processed_at is None


def test_duplicate_event_has_one_record_and_one_logical_effect(
    stripe_webhook_context: tuple,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context
    event = stripe_event("evt_duplicate", "payment_intent.succeeded")

    first = post_signed(client, event)
    second = post_signed(client, event)

    assert first.status_code == second.status_code == 200
    assert list(repository.events) == ["evt_duplicate"]
    assert repository.process_calls == 1
    assert payment.status == PaymentStatus.APPROVED


def test_two_succeeded_events_for_same_payment_are_both_processed(
    stripe_webhook_context: tuple,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context

    first = post_signed(
        client,
        stripe_event("evt_success_a", "payment_intent.succeeded"),
    )
    second = post_signed(
        client,
        stripe_event("evt_success_b", "payment_intent.succeeded"),
    )

    assert first.status_code == second.status_code == 200
    assert len(repository.events) == 2
    assert repository.process_calls == 2
    assert payment.status == PaymentStatus.APPROVED
    assert all(event.processed_at is not None for event in repository.events.values())


def test_out_of_order_failure_is_audited_without_reversing_approved_payment(
    stripe_webhook_context: tuple,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context
    payment.status = PaymentStatus.APPROVED

    response = post_signed(
        client,
        stripe_event("evt_out_of_order", "payment_intent.payment_failed"),
    )

    assert response.status_code == 200
    assert payment.status == PaymentStatus.APPROVED
    assert repository.events["evt_out_of_order"].processed_at is None


def test_database_failure_returns_retryable_503_and_repository_recovers(
    stripe_webhook_context: tuple,
) -> None:
    client, repository, payment, _, _ = stripe_webhook_context
    repository.fail_next_create = True

    failed = post_signed(
        client,
        stripe_event("evt_db_retry", "payment_intent.succeeded"),
    )
    recovered = post_signed(
        client,
        stripe_event("evt_db_retry", "payment_intent.succeeded"),
    )

    assert failed.status_code == 503
    assert recovered.status_code == 200
    assert payment.status == PaymentStatus.APPROVED
    assert list(repository.events) == ["evt_db_retry"]


def test_configuration_stores_webhook_secret_as_secret_str() -> None:
    settings = Settings(
        jwt_secret_key="test-secret-key-that-is-at-least-32-characters",
        stripe_webhook_secret=WEBHOOK_SECRET,
    )

    assert isinstance(settings.stripe_webhook_secret, SecretStr)
    assert WEBHOOK_SECRET not in repr(settings)


def test_stripe_webhook_openapi_is_public_and_does_not_parse_a_model_body() -> None:
    operation = app.openapi()["paths"]["/api/v1/webhooks/stripe"]["post"]

    assert "security" not in operation
    assert "requestBody" not in operation
    assert operation["parameters"] == [
        {
            "name": "Stripe-Signature",
            "in": "header",
            "required": True,
            "schema": {"type": "string"},
        }
    ]
    assert {"200", "400", "503"} <= set(operation["responses"])
    serialized = str(operation).lower()
    assert "stripe_webhook_secret" not in serialized
    assert "stripe-signature" in serialized
