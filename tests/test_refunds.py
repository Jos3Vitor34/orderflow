from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
import stripe
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.dependencies import get_current_user, get_refund_service
from app.integrations.stripe import (
    StripeAuthenticationError,
    StripeConfigurationError,
    StripeInvalidRequestError,
    StripeTemporaryError,
)
from app.integrations.stripe_refund import (
    StripeRefund,
    StripeRefundGatewayAdapter,
)
from app.main import app
from app.models.payment import Payment, PaymentStatus
from app.models.refund import Refund, RefundStatus
from app.models.user import User
from app.repositories.refund import RefundConstraintError
from app.services.refund import RefundService


class FakeRefundGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.error: Exception | None = None
        self.status = "succeeded"
        self.amount_override: int | None = None
        self.currency_override: str | None = None
        self._by_key: dict[str, StripeRefund] = {}

    def create_refund(
        self,
        *,
        payment_intent: str,
        amount: int,
        reason: str | None,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> StripeRefund:
        self.calls.append(
            {
                "payment_intent": payment_intent,
                "amount": amount,
                "reason": reason,
                "metadata": metadata,
                "idempotency_key": idempotency_key,
            }
        )
        if self.error is not None:
            error = self.error
            self.error = None
            raise error
        existing = self._by_key.get(idempotency_key)
        if existing is not None:
            return existing
        refund = StripeRefund(
            id=f"re_test_{len(self._by_key) + 1}",
            payment_intent=payment_intent,
            amount=self.amount_override or amount,
            currency=self.currency_override or "brl",
            status=self.status,
            reason=reason,
            failure_reason=None,
            created=1_700_000_000 + len(self._by_key),
        )
        self._by_key[idempotency_key] = refund
        return refund


class InMemoryRefundRepository:
    def __init__(self) -> None:
        self.payments: dict[int, Payment] = {}
        self.refunds: dict[int, Refund] = {}
        self.fail_next_commit = False
        self._next_id = 1

    def get_payment_for_update(self, payment_id: int) -> Payment | None:
        return self.payments.get(payment_id)

    def get_by_idempotency_key_hash(
        self, payment_id: int, key_hash: str
    ) -> Refund | None:
        return next(
            (
                refund
                for refund in self.refunds.values()
                if refund.payment_id == payment_id
                and refund.idempotency_key_hash == key_hash
            ),
            None,
        )

    def get_by_id_for_update(self, refund_id: int) -> Refund | None:
        return self.refunds.get(refund_id)

    def active_amount(self, payment_id: int) -> Decimal:
        return sum(
            (
                refund.amount
                for refund in self.refunds.values()
                if refund.payment_id == payment_id
                and refund.status
                in {
                    RefundStatus.PENDING,
                    RefundStatus.REQUIRES_ACTION,
                    RefundStatus.SUCCEEDED,
                }
            ),
            Decimal("0"),
        )

    def succeeded_amount(self, payment_id: int) -> Decimal:
        return sum(
            (
                refund.amount
                for refund in self.refunds.values()
                if refund.payment_id == payment_id
                and refund.status == RefundStatus.SUCCEEDED
            ),
            Decimal("0"),
        )

    def create_reservation(self, **values: object) -> Refund:
        refund = Refund(
            id=self._next_id,
            provider="stripe",
            provider_refund_id=None,
            status=RefundStatus.PENDING,
            failure_reason=None,
            provider_created_at=None,
            last_provider_event_created_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            **values,
        )
        self._next_id += 1
        self.refunds[refund.id] = refund
        return refund

    def release_reservation(self, refund: Refund) -> None:
        self.refunds.pop(refund.id)

    def commit_provider_result(
        self,
        *,
        refund: Refund,
        payment: Payment,
        provider_refund_id: str,
        status: RefundStatus,
        reason: str | None,
        failure_reason: str | None,
        provider_created_at: datetime,
        payment_status: PaymentStatus,
    ) -> Refund:
        if self.fail_next_commit:
            self.fail_next_commit = False
            refund.status = RefundStatus.PENDING
            payment.status = PaymentStatus.APPROVED
            raise RefundConstraintError
        refund.provider_refund_id = provider_refund_id
        refund.status = status
        refund.reason = reason
        refund.failure_reason = failure_reason
        refund.provider_created_at = provider_created_at
        payment.status = payment_status
        return refund

    def get_by_id(self, refund_id: int) -> Refund | None:
        return self.refunds.get(refund_id)

    def payment_exists(self, payment_id: int) -> bool:
        return payment_id in self.payments

    def list_page(
        self, *, payment_id: int, offset: int, limit: int
    ) -> tuple[list[Refund], int]:
        values = sorted(
            (
                refund
                for refund in self.refunds.values()
                if refund.payment_id == payment_id
            ),
            key=lambda refund: refund.id,
        )
        return values[offset : offset + limit], len(values)


@pytest.fixture
def refund_context() -> Generator[
    tuple[TestClient, InMemoryRefundRepository, FakeRefundGateway, Payment]
]:
    now = datetime.now(UTC)
    repository = InMemoryRefundRepository()
    payment = Payment(
        id=1,
        order_id=1,
        provider="stripe",
        provider_reference="pi_refundable",
        amount=Decimal("239.82"),
        status=PaymentStatus.APPROVED,
        created_at=now,
        updated_at=now,
    )
    repository.payments[payment.id] = payment
    gateway = FakeRefundGateway()
    service = RefundService(repository, gateway, currency="brl")  # type: ignore[arg-type]
    user = User(
        id=1,
        full_name="Refund Tester",
        email="refunds@example.com",
        hashed_password="not-used",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_refund_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield client, repository, gateway, payment
    app.dependency_overrides.pop(get_refund_service, None)
    app.dependency_overrides.pop(get_current_user, None)


def create_refund(
    client: TestClient,
    *,
    body: dict[str, object] | None = None,
    key: str = "refund-attempt-1",
):
    return client.post(
        "/api/v1/payments/1/refunds",
        json=body or {},
        headers={"Idempotency-Key": key},
    )


def test_full_refund_uses_remaining_balance_and_marks_payment_refunded(
    refund_context: tuple,
) -> None:
    client, repository, gateway, payment = refund_context

    response = create_refund(client)

    assert response.status_code == 201
    assert response.json()["amount"] == "239.82"
    assert response.json()["status"] == "succeeded"
    assert payment.status == PaymentStatus.REFUNDED
    assert gateway.calls[0]["amount"] == 23982
    assert len(repository.refunds) == 1


def test_partial_and_multiple_refunds_use_persisted_aggregate(
    refund_context: tuple,
) -> None:
    client, _, gateway, payment = refund_context

    first = create_refund(client, body={"amount": "100.00"}, key="partial-1")
    second = create_refund(client, body={"amount": "39.82"}, key="partial-2")
    final = create_refund(client, key="remaining")

    assert first.status_code == second.status_code == final.status_code == 201
    assert [call["amount"] for call in gateway.calls] == [10000, 3982, 10000]
    assert payment.status == PaymentStatus.REFUNDED


@pytest.mark.parametrize("amount", ["0", "-0.01", "1.001"])
def test_invalid_decimal_amount_is_rejected_before_stripe(
    refund_context: tuple,
    amount: str,
) -> None:
    client, repository, gateway, _ = refund_context

    response = create_refund(client, body={"amount": amount})

    assert response.status_code == 422
    assert gateway.calls == []
    assert repository.refunds == {}


def test_amount_above_remaining_balance_is_rejected(
    refund_context: tuple,
) -> None:
    client, repository, gateway, _ = refund_context

    response = create_refund(client, body={"amount": "239.83"})

    assert response.status_code == 409
    assert gateway.calls == []
    assert repository.refunds == {}


def test_missing_payment_returns_404(refund_context: tuple) -> None:
    client, _, gateway, _ = refund_context

    response = client.post(
        "/api/v1/payments/999/refunds",
        json={},
        headers={"Idempotency-Key": "missing"},
    )

    assert response.status_code == 404
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("status_value", "provider", "reference"),
    [
        (PaymentStatus.PENDING, "stripe", "pi_refundable"),
        (PaymentStatus.APPROVED, "manual", "pi_refundable"),
        (PaymentStatus.APPROVED, "stripe", None),
        (PaymentStatus.REFUNDED, "stripe", "pi_refundable"),
    ],
)
def test_non_refundable_payment_is_rejected(
    refund_context: tuple,
    status_value: PaymentStatus,
    provider: str,
    reference: str | None,
) -> None:
    client, repository, gateway, payment = refund_context
    payment.status = status_value
    payment.provider = provider
    payment.provider_reference = reference

    response = create_refund(client)

    assert response.status_code == 409
    assert gateway.calls == []
    assert repository.refunds == {}


@pytest.mark.parametrize(
    "reason",
    ["duplicate", "fraudulent", "requested_by_customer"],
)
def test_valid_reasons_are_forwarded(
    refund_context: tuple,
    reason: str,
) -> None:
    client, _, gateway, _ = refund_context

    response = create_refund(client, body={"amount": "1.00", "reason": reason})

    assert response.status_code == 201
    assert gateway.calls[0]["reason"] == reason


def test_invalid_reason_and_extra_fields_are_rejected(refund_context: tuple) -> None:
    client, _, gateway, _ = refund_context

    invalid_reason = create_refund(client, body={"reason": "other"})
    extra_field = create_refund(client, body={"currency": "usd"}, key="extra")

    assert invalid_reason.status_code == extra_field.status_code == 422
    assert gateway.calls == []


def test_idempotency_header_is_required(refund_context: tuple) -> None:
    client, repository, gateway, _ = refund_context

    response = client.post("/api/v1/payments/1/refunds", json={})

    assert response.status_code == 422
    assert repository.refunds == {}
    assert gateway.calls == []


def test_same_key_and_payload_reuses_refund_without_second_stripe_call(
    refund_context: tuple,
) -> None:
    client, repository, gateway, _ = refund_context
    body = {"amount": "10.00", "reason": "duplicate"}

    first = create_refund(client, body=body, key="same-key")
    second = create_refund(client, body=body, key="same-key")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(gateway.calls) == 1
    assert len(repository.refunds) == 1
    refund = next(iter(repository.refunds.values()))
    assert refund.idempotency_key_hash != "same-key"
    assert len(refund.idempotency_key_hash or "") == 64


def test_same_key_with_different_payload_conflicts(refund_context: tuple) -> None:
    client, repository, gateway, _ = refund_context

    first = create_refund(client, body={"amount": "10.00"}, key="same-key")
    second = create_refund(client, body={"amount": "11.00"}, key="same-key")

    assert first.status_code == 201
    assert second.status_code == 409
    assert len(repository.refunds) == 1
    assert len(gateway.calls) == 1


def test_exact_minor_units_metadata_and_namespaced_stripe_key(
    refund_context: tuple,
) -> None:
    client, _, gateway, _ = refund_context

    response = create_refund(client, body={"amount": "19.90"}, key="metadata")

    assert response.status_code == 201
    call = gateway.calls[0]
    assert call["amount"] == 1990
    assert call["payment_intent"] == "pi_refundable"
    assert call["metadata"] == {
        "orderflow_refund_id": "1",
        "orderflow_payment_id": "1",
    }
    assert str(call["idempotency_key"]).startswith("orderflow:stripe-refund:v1:1:")
    assert "metadata" not in response.json()


@pytest.mark.parametrize(
    ("error", "expected_status", "reservation_retained"),
    [
        (StripeConfigurationError(), 503, False),
        (StripeAuthenticationError(), 502, False),
        (StripeInvalidRequestError(), 502, False),
        (StripeTemporaryError(), 503, True),
    ],
)
def test_stripe_failures_are_sanitized_and_reservation_policy_is_safe(
    refund_context: tuple,
    error: Exception,
    expected_status: int,
    reservation_retained: bool,
) -> None:
    client, repository, gateway, payment = refund_context
    gateway.error = error

    response = create_refund(client)

    assert response.status_code == expected_status
    assert len(repository.refunds) == int(reservation_retained)
    assert payment.status == PaymentStatus.APPROVED
    assert "secret" not in response.text.lower()


def test_transient_failure_recovers_with_same_stripe_idempotency_key(
    refund_context: tuple,
) -> None:
    client, repository, gateway, payment = refund_context
    gateway.error = StripeTemporaryError()

    failed = create_refund(client, body={"amount": "20.00"}, key="recover")
    recovered = create_refund(client, body={"amount": "20.00"}, key="recover")

    assert failed.status_code == 503
    assert recovered.status_code == 200
    assert gateway.calls[0]["idempotency_key"] == gateway.calls[1]["idempotency_key"]
    assert len(repository.refunds) == 1
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


def test_local_failure_after_stripe_recovers_without_second_logical_refund(
    refund_context: tuple,
) -> None:
    client, repository, gateway, payment = refund_context
    repository.fail_next_commit = True

    failed = create_refund(client, body={"amount": "20.00"}, key="db-recover")
    recovered = create_refund(client, body={"amount": "20.00"}, key="db-recover")

    assert failed.status_code == 503
    assert recovered.status_code == 200
    assert len(repository.refunds) == 1
    assert gateway.calls[0]["idempotency_key"] == gateway.calls[1]["idempotency_key"]
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


def test_authenticated_refund_queries_are_paginated(refund_context: tuple) -> None:
    client, _, _, _ = refund_context
    created = create_refund(client, body={"amount": "10.00"})

    listing = client.get("/api/v1/payments/1/refunds?page=1&page_size=10")
    detail = client.get(f"/api/v1/refunds/{created.json()['id']}")
    missing = client.get("/api/v1/refunds/999")

    assert listing.status_code == detail.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"] == [detail.json()]
    assert missing.status_code == 404


def test_refund_endpoints_require_bearer_authentication() -> None:
    with TestClient(app) as client:
        assert client.post("/api/v1/payments/1/refunds", json={}).status_code == 401
        assert client.get("/api/v1/payments/1/refunds").status_code == 401
        assert client.get("/api/v1/refunds/1").status_code == 401


def test_official_refund_adapter_uses_safe_contract() -> None:
    captured: dict[str, object] = {}

    class Refunds:
        @staticmethod
        def create(params: dict[str, object], options: dict[str, str]):
            captured.update(params=params, options=options)
            return SimpleNamespace(
                id="re_adapter",
                payment_intent="pi_adapter",
                amount=1990,
                currency="brl",
                status="pending",
                reason="duplicate",
                failure_reason=None,
                created=1_700_000_000,
            )

    adapter = StripeRefundGatewayAdapter(
        SecretStr("sk_test_placeholder"),
        client=SimpleNamespace(v1=SimpleNamespace(refunds=Refunds())),  # type: ignore[arg-type]
    )

    result = adapter.create_refund(
        payment_intent="pi_adapter",
        amount=1990,
        reason="duplicate",
        metadata={"orderflow_refund_id": "10"},
        idempotency_key="stable-refund-key",
    )

    assert captured == {
        "params": {
            "payment_intent": "pi_adapter",
            "amount": 1990,
            "reason": "duplicate",
            "metadata": {"orderflow_refund_id": "10"},
        },
        "options": {"idempotency_key": "stable-refund-key"},
    }
    assert result.id == "re_adapter"


@pytest.mark.parametrize(
    ("sdk_error", "expected"),
    [
        (stripe.AuthenticationError("bad key"), StripeAuthenticationError),
        (
            stripe.InvalidRequestError("invalid", param="amount"),
            StripeInvalidRequestError,
        ),
        (stripe.APIConnectionError("network"), StripeTemporaryError),
    ],
)
def test_official_refund_adapter_maps_sdk_errors(
    sdk_error: stripe.StripeError,
    expected: type[Exception],
) -> None:
    class Refunds:
        @staticmethod
        def create(params: dict[str, object], options: dict[str, str]) -> None:
            raise sdk_error

    adapter = StripeRefundGatewayAdapter(
        SecretStr("sk_test_placeholder"),
        client=SimpleNamespace(v1=SimpleNamespace(refunds=Refunds())),  # type: ignore[arg-type]
    )

    with pytest.raises(expected):
        adapter.create_refund(
            payment_intent="pi_adapter",
            amount=100,
            reason=None,
            metadata={},
            idempotency_key="key",
        )


def test_missing_or_live_secret_never_reaches_network() -> None:
    for secret in (None, SecretStr("sk_live_forbidden")):
        adapter = StripeRefundGatewayAdapter(secret)
        with pytest.raises(StripeConfigurationError):
            adapter.create_refund(
                payment_intent="pi_adapter",
                amount=100,
                reason=None,
                metadata={},
                idempotency_key="key",
            )


def test_refund_openapi_contract_is_authenticated_and_explicit() -> None:
    schema = app.openapi()
    create_operation = schema["paths"]["/api/v1/payments/{payment_id}/refunds"]["post"]

    assert create_operation["security"] == [{"OAuth2PasswordBearer": []}]
    assert {"200", "201", "401", "404", "409", "422", "502", "503"} <= set(
        create_operation["responses"]
    )
    assert {"RefundCreate", "RefundResponse", "RefundListResponse"} <= set(
        schema["components"]["schemas"]
    )
    serialized = str(create_operation).lower()
    assert "secret" not in serialized
    assert "stripe-signature" not in serialized
