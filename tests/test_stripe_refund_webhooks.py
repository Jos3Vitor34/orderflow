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
from app.integrations.stripe_webhook import StripeWebhookVerifier
from app.main import app
from app.models.payment import Payment, PaymentStatus
from app.models.refund import Refund, RefundStatus
from app.models.webhook_event import WebhookEvent
from app.repositories.webhook_event import WebhookEventConstraintError
from app.services.stripe_webhook import StripeWebhookService

WEBHOOK_SECRET = "whsec_phase14_refunds_test_only"


class InMemoryRefundWebhookRepository:
    def __init__(self) -> None:
        self.events: dict[str, WebhookEvent] = {}
        self.payments: dict[str, Payment] = {}
        self.refunds: dict[int, Refund] = {}
        self.fail_next_commit = False
        self._next_event_id = 1
        self._next_refund_id = 1

    def get_by_provider_event_id(self, provider_event_id: str) -> WebhookEvent | None:
        return self.events.get(provider_event_id)

    def create_received(self, **values: object) -> WebhookEvent:
        event = WebhookEvent(
            id=self._next_event_id,
            received_at=datetime.now(UTC),
            processed_at=None,
            **values,
        )
        self._next_event_id += 1
        return event

    def commit_received(self, event: WebhookEvent) -> WebhookEvent:
        self.events[event.provider_event_id] = event
        return event

    def get_payment_for_update(self, provider_reference: str) -> Payment | None:
        return self.payments.get(provider_reference)

    def commit_processed(
        self,
        event: WebhookEvent,
        payment: Payment,
        status: PaymentStatus,
    ) -> WebhookEvent:
        payment.status = status
        event.processed_at = datetime.now(UTC)
        self.events[event.provider_event_id] = event
        return event

    def get_refund_by_provider_id_for_update(
        self, provider_refund_id: str
    ) -> Refund | None:
        return next(
            (
                refund
                for refund in self.refunds.values()
                if refund.provider_refund_id == provider_refund_id
            ),
            None,
        )

    def get_refund_by_id_for_update(self, refund_id: int) -> Refund | None:
        return self.refunds.get(refund_id)

    def create_external_refund(self, **values: object) -> Refund:
        event_created_at = values.pop("event_created_at")
        refund = Refund(
            id=self._next_refund_id,
            provider="stripe",
            idempotency_key_hash=None,
            request_fingerprint=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_provider_event_created_at=event_created_at,
            **values,
        )
        self._next_refund_id += 1
        self.refunds[refund.id] = refund
        return refund

    def succeeded_refund_amount(self, payment_id: int) -> Decimal:
        return sum(
            (
                refund.amount
                for refund in self.refunds.values()
                if refund.payment_id == payment_id
                and refund.status == RefundStatus.SUCCEEDED
            ),
            Decimal("0"),
        )

    def commit_refund_processed(
        self,
        *,
        webhook_event: WebhookEvent,
        refund: Refund,
        payment: Payment,
        refund_status: RefundStatus,
        payment_status: PaymentStatus,
        provider_refund_id: str,
        reason: str | None,
        failure_reason: str | None,
        provider_created_at: datetime,
        event_created_at: datetime,
    ) -> WebhookEvent:
        if self.fail_next_commit:
            self.fail_next_commit = False
            if refund.idempotency_key_hash is None:
                self.refunds.pop(refund.id, None)
            raise WebhookEventConstraintError
        refund.provider_refund_id = provider_refund_id
        refund.status = refund_status
        refund.reason = reason
        refund.failure_reason = failure_reason
        refund.provider_created_at = provider_created_at
        refund.last_provider_event_created_at = event_created_at
        payment.status = payment_status
        webhook_event.processed_at = datetime.now(UTC)
        self.events[webhook_event.provider_event_id] = webhook_event
        return webhook_event


def refund_event(
    event_id: str,
    event_type: str,
    *,
    refund_id: str = "re_phase14",
    payment_intent: str = "pi_phase14",
    amount: int = 10000,
    currency: str = "brl",
    status: str = "pending",
    event_created: int = 1_700_000_100,
    metadata: dict[str, str] | None = None,
    failure_reason: str | None = None,
) -> dict[str, object]:
    return {
        "id": event_id,
        "object": "event",
        "api_version": "2025-04-30.basil",
        "created": event_created,
        "livemode": False,
        "type": event_type,
        "data": {
            "object": {
                "id": refund_id,
                "object": "refund",
                "payment_intent": payment_intent,
                "amount": amount,
                "currency": currency,
                "status": status,
                "reason": "requested_by_customer",
                "failure_reason": failure_reason,
                "created": 1_700_000_000,
                "metadata": metadata or {},
            }
        },
    }


def signed_request(
    client: TestClient,
    event: dict[str, object],
    *,
    body_override: bytes | None = None,
):
    original = json.dumps(event, separators=(",", ":")).encode()
    signature = stripe.WebhookSignature.generate_signature_header(
        original.decode(),
        WEBHOOK_SECRET,
        timestamp=int(time.time()),
    )
    return client.post(
        "/api/v1/webhooks/stripe",
        content=body_override or original,
        headers={"Stripe-Signature": signature},
    )


@pytest.fixture
def refund_webhook_context() -> Generator[
    tuple[TestClient, InMemoryRefundWebhookRepository, Payment]
]:
    now = datetime.now(UTC)
    repository = InMemoryRefundWebhookRepository()
    payment = Payment(
        id=1,
        order_id=1,
        provider="stripe",
        provider_reference="pi_phase14",
        amount=Decimal("239.82"),
        status=PaymentStatus.APPROVED,
        created_at=now,
        updated_at=now,
    )
    repository.payments["pi_phase14"] = payment
    service = StripeWebhookService(repository, currency="brl")  # type: ignore[arg-type]
    verifier = StripeWebhookVerifier(SecretStr(WEBHOOK_SECRET))
    app.dependency_overrides[get_stripe_webhook_service] = lambda: service
    app.dependency_overrides[get_stripe_webhook_verifier] = lambda: verifier
    with TestClient(app) as client:
        yield client, repository, payment
    app.dependency_overrides.pop(get_stripe_webhook_service, None)
    app.dependency_overrides.pop(get_stripe_webhook_verifier, None)


def local_refund(
    repository: InMemoryRefundWebhookRepository,
    *,
    status: RefundStatus = RefundStatus.PENDING,
    amount: Decimal = Decimal("100.00"),
    provider_refund_id: str | None = "re_phase14",
    last_event: datetime | None = None,
) -> Refund:
    refund = Refund(
        id=repository._next_refund_id,
        payment_id=1,
        provider="stripe",
        provider_refund_id=provider_refund_id,
        amount=amount,
        currency="brl",
        status=status,
        reason="requested_by_customer",
        failure_reason=None,
        idempotency_key_hash="a" * 64,
        request_fingerprint="b" * 64,
        provider_created_at=datetime.fromtimestamp(1_700_000_000, tz=UTC),
        last_provider_event_created_at=last_event,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repository.refunds[refund.id] = refund
    repository._next_refund_id += 1
    return refund


def test_refund_created_pending_is_persisted_and_processed(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context

    response = signed_request(
        client,
        refund_event("evt_refund_pending", "refund.created"),
    )

    assert response.status_code == 200
    refund = next(iter(repository.refunds.values()))
    assert refund.provider_refund_id == "re_phase14"
    assert refund.status == RefundStatus.PENDING
    assert payment.status == PaymentStatus.APPROVED
    assert repository.events["evt_refund_pending"].processed_at is not None


def test_refund_created_succeeded_from_dashboard_updates_partial_payment(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context

    response = signed_request(
        client,
        refund_event(
            "evt_dashboard_refund",
            "refund.created",
            status="succeeded",
        ),
    )

    assert response.status_code == 200
    assert len(repository.refunds) == 1
    assert next(iter(repository.refunds.values())).status == RefundStatus.SUCCEEDED
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


def test_refund_updated_to_succeeded_updates_existing_local_refund(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context
    refund = local_refund(repository)

    response = signed_request(
        client,
        refund_event("evt_refund_updated", "refund.updated", status="succeeded"),
    )

    assert response.status_code == 200
    assert len(repository.refunds) == 1
    assert refund.status == RefundStatus.SUCCEEDED
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


@pytest.mark.parametrize("event_type", ["refund.failed", "refund.updated"])
def test_failed_or_canceled_refund_does_not_count_as_returned_money(
    refund_webhook_context: tuple,
    event_type: str,
) -> None:
    client, repository, payment = refund_webhook_context
    target = "failed" if event_type == "refund.failed" else "canceled"
    failure = "declined" if target == "failed" else "merchant_request"

    response = signed_request(
        client,
        refund_event(
            f"evt_{target}",
            event_type,
            status=target,
            failure_reason=failure,
        ),
    )

    assert response.status_code == 200
    refund = next(iter(repository.refunds.values()))
    assert refund.status.value == target
    assert refund.failure_reason == failure
    assert payment.status == PaymentStatus.APPROVED


def test_total_refund_marks_payment_refunded(refund_webhook_context: tuple) -> None:
    client, _, payment = refund_webhook_context

    response = signed_request(
        client,
        refund_event(
            "evt_total_refund",
            "refund.created",
            amount=23982,
            status="succeeded",
        ),
    )

    assert response.status_code == 200
    assert payment.status == PaymentStatus.REFUNDED
    assert payment.amount == Decimal("239.82")


def test_metadata_correlates_api_reservation_without_replacing_payment_identity(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context
    refund = local_refund(repository, provider_refund_id=None)

    response = signed_request(
        client,
        refund_event(
            "evt_correlated",
            "refund.created",
            status="succeeded",
            metadata={"orderflow_refund_id": str(refund.id)},
        ),
    )

    assert response.status_code == 200
    assert len(repository.refunds) == 1
    assert refund.provider_refund_id == "re_phase14"
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


@pytest.mark.parametrize(
    ("event_id", "payment_intent", "currency"),
    [
        ("evt_missing_payment", "pi_missing", "brl"),
        ("evt_wrong_currency", "pi_phase14", "usd"),
    ],
)
def test_incompatible_refund_is_audited_without_effect(
    refund_webhook_context: tuple,
    event_id: str,
    payment_intent: str,
    currency: str,
) -> None:
    client, repository, payment = refund_webhook_context

    response = signed_request(
        client,
        refund_event(
            event_id,
            "refund.created",
            payment_intent=payment_intent,
            currency=currency,
            status="succeeded",
        ),
    )

    assert response.status_code == 200
    assert repository.events[event_id].processed_at is None
    assert repository.refunds == {}
    assert payment.status == PaymentStatus.APPROVED


def test_wrong_payment_provider_is_audited(refund_webhook_context: tuple) -> None:
    client, repository, payment = refund_webhook_context
    payment.provider = "manual"

    response = signed_request(
        client,
        refund_event("evt_wrong_provider", "refund.created", status="succeeded"),
    )

    assert response.status_code == 200
    assert repository.events["evt_wrong_provider"].processed_at is None
    assert repository.refunds == {}


def test_existing_refund_amount_mismatch_is_audited(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context
    refund = local_refund(repository, amount=Decimal("1.00"))

    response = signed_request(
        client,
        refund_event("evt_amount_mismatch", "refund.updated", status="succeeded"),
    )

    assert response.status_code == 200
    assert refund.status == RefundStatus.PENDING
    assert payment.status == PaymentStatus.APPROVED
    assert repository.events["evt_amount_mismatch"].processed_at is None


@pytest.mark.parametrize(
    "event_type",
    ["charge.refunded", "payment_intent.processing"],
)
def test_charge_refunded_and_unknown_events_remain_audit_only(
    refund_webhook_context: tuple,
    event_type: str,
) -> None:
    client, repository, payment = refund_webhook_context

    response = signed_request(
        client,
        refund_event(f"evt_{event_type}", event_type, status="succeeded"),
    )

    assert response.status_code == 200
    assert repository.refunds == {}
    assert payment.status == PaymentStatus.APPROVED
    assert repository.events[f"evt_{event_type}"].processed_at is None


def test_duplicate_event_has_one_refund_and_one_effect(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context
    event = refund_event("evt_duplicate_refund", "refund.created", status="succeeded")

    first = signed_request(client, event)
    second = signed_request(client, event)

    assert first.status_code == second.status_code == 200
    assert len(repository.events) == len(repository.refunds) == 1
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


def test_different_events_update_same_refund_without_duplication(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context

    created = signed_request(
        client,
        refund_event("evt_created", "refund.created", status="pending"),
    )
    updated = signed_request(
        client,
        refund_event(
            "evt_updated",
            "refund.updated",
            status="succeeded",
            event_created=1_700_000_200,
        ),
    )

    assert created.status_code == updated.status_code == 200
    assert len(repository.events) == 2
    assert len(repository.refunds) == 1
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


def test_refund_updated_before_created_can_create_record(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context

    response = signed_request(
        client,
        refund_event("evt_updated_first", "refund.updated", status="succeeded"),
    )

    assert response.status_code == 200
    assert len(repository.refunds) == 1
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


def test_older_or_regressive_event_is_audited_without_state_regression(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context
    refund = local_refund(
        repository,
        status=RefundStatus.SUCCEEDED,
        last_event=datetime.fromtimestamp(1_700_000_200, tz=UTC),
    )
    payment.status = PaymentStatus.PARTIALLY_REFUNDED

    response = signed_request(
        client,
        refund_event(
            "evt_old_pending",
            "refund.updated",
            status="pending",
            event_created=1_700_000_100,
        ),
    )

    assert response.status_code == 200
    assert refund.status == RefundStatus.SUCCEEDED
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED
    assert repository.events["evt_old_pending"].processed_at is None


def test_multiple_succeeded_refunds_use_aggregate_for_final_state(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context
    local_refund(
        repository,
        status=RefundStatus.SUCCEEDED,
        amount=Decimal("100.00"),
        provider_refund_id="re_first",
    )
    payment.status = PaymentStatus.PARTIALLY_REFUNDED

    response = signed_request(
        client,
        refund_event(
            "evt_final_partial",
            "refund.created",
            refund_id="re_second",
            amount=13982,
            status="succeeded",
        ),
    )

    assert response.status_code == 200
    assert len(repository.refunds) == 2
    assert payment.status == PaymentStatus.REFUNDED


def test_raw_body_tampering_rejects_refund_before_audit(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context
    event = refund_event("evt_tampered_refund", "refund.created", status="succeeded")
    original = json.dumps(event, separators=(",", ":")).encode()

    response = signed_request(client, event, body_override=original + b" ")

    assert response.status_code == 400
    assert repository.events == repository.refunds == {}
    assert payment.status == PaymentStatus.APPROVED


def test_database_failure_rolls_back_and_retry_recovers(
    refund_webhook_context: tuple,
) -> None:
    client, repository, payment = refund_webhook_context
    repository.fail_next_commit = True
    event = refund_event("evt_db_refund", "refund.created", status="succeeded")

    failed = signed_request(client, event)
    recovered = signed_request(client, event)

    assert failed.status_code == 503
    assert recovered.status_code == 200
    assert len(repository.events) == len(repository.refunds) == 1
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED
