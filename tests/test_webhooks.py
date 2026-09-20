from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_webhook_event_service
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.models.product import Product
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.repositories.webhook_event import WebhookEventConstraintError
from app.services.webhook_event import WebhookEventService


class InMemoryWebhookEventRepository:
    def __init__(self) -> None:
        self.webhook_events: dict[int, WebhookEvent] = {}
        self.payments: dict[str, Payment] = {}
        self.process_calls = 0
        self.fail_next_create = False
        self._next_id = 1

    def get_by_provider_event_id(self, provider_event_id: str) -> WebhookEvent | None:
        return next(
            (
                event
                for event in self.webhook_events.values()
                if event.provider_event_id == provider_event_id
            ),
            None,
        )

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
        now = datetime.now(UTC)
        event = WebhookEvent(
            id=self._next_id,
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=payload,
            received_at=now,
            processed_at=None,
        )
        self._next_id += 1
        return event

    def get_payment_for_update(self, provider_reference: str) -> Payment | None:
        return self.payments.get(provider_reference)

    def commit_received(self, webhook_event: WebhookEvent) -> WebhookEvent:
        self.webhook_events[webhook_event.id] = webhook_event
        return webhook_event

    def commit_processed(
        self,
        webhook_event: WebhookEvent,
        payment: Payment,
        new_status: PaymentStatus,
    ) -> WebhookEvent:
        self.process_calls += 1
        payment.status = new_status
        webhook_event.processed_at = datetime.now(UTC)
        self.webhook_events[webhook_event.id] = webhook_event
        return webhook_event

    def get_by_id(self, webhook_event_id: int) -> WebhookEvent | None:
        return self.webhook_events.get(webhook_event_id)

    def list_page(self, *, offset: int, limit: int) -> tuple[list[WebhookEvent], int]:
        events = sorted(self.webhook_events.values(), key=lambda event: event.id)
        return events[offset : offset + limit], len(events)


@pytest.fixture
def webhook_context() -> Generator[
    tuple[
        TestClient,
        InMemoryWebhookEventRepository,
        Order,
        Product,
    ]
]:
    repository = InMemoryWebhookEventRepository()
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
        sku="WEBHOOK-PRODUCT",
        name="Webhook Product",
        description=None,
        price=Decimal("239.82"),
        stock=7,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    repository.payments["pay-001"] = Payment(
        id=1,
        order_id=order.id,
        provider="provider-a",
        provider_reference="pay-001",
        amount=Decimal("239.82"),
        status=PaymentStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    repository.payments["pay-failed"] = Payment(
        id=2,
        order_id=order.id,
        provider="provider-a",
        provider_reference="pay-failed",
        amount=Decimal("19.90"),
        status=PaymentStatus.FAILED,
        created_at=now,
        updated_at=now,
    )

    service = WebhookEventService(repository)  # type: ignore[arg-type]
    authenticated_user = User(
        id=1,
        full_name="Authenticated User",
        email="user@example.com",
        hashed_password="not-used",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_webhook_event_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: authenticated_user

    with TestClient(app) as client:
        yield client, repository, order, product

    app.dependency_overrides.pop(get_webhook_event_service, None)
    app.dependency_overrides.pop(get_current_user, None)


def webhook_payload(
    *,
    event_id: str = "evt-001",
    event_type: str = "payment.approved",
    provider_reference: str = "pay-001",
) -> dict[str, object]:
    return {
        "provider_event_id": event_id,
        "event_type": event_type,
        "payload": {
            "provider_reference": provider_reference,
            "status": event_type.removeprefix("payment."),
            "metadata": {"source": "test", "attempt": 1},
        },
    }


def test_webhook_receiver_does_not_require_user_jwt(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, _, _, _ = webhook_context
    app.dependency_overrides.pop(get_current_user, None)

    response = client.post(
        "/api/v1/webhooks/provider-a",
        json={
            "provider_event_id": "evt-unknown",
            "event_type": "provider.ping",
            "payload": {"healthy": True},
        },
    )

    assert response.status_code == 201


def test_approved_event_is_persisted_and_updates_payment_once(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, repository, order, product = webhook_context
    amount_before = repository.payments["pay-001"].amount
    stock_before = product.stock

    first = client.post("/api/v1/webhooks/provider-a", json=webhook_payload())
    second = client.post("/api/v1/webhooks/provider-a", json=webhook_payload())

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(repository.webhook_events) == 1
    assert repository.process_calls == 1
    assert repository.payments["pay-001"].status == PaymentStatus.APPROVED
    assert repository.payments["pay-001"].amount == amount_before
    assert order.status == OrderStatus.PENDING
    assert product.stock == stock_before
    assert first.json()["processed_at"] is not None
    assert first.json()["payload"] == webhook_payload()["payload"]


def test_failed_and_refunded_events_reuse_payment_transitions(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, repository, _, _ = webhook_context

    failed = client.post(
        "/api/v1/webhooks/provider-a",
        json=webhook_payload(
            event_id="evt-failed",
            event_type="payment.failed",
        ),
    )
    repository.payments["pay-001"].status = PaymentStatus.APPROVED
    refunded = client.post(
        "/api/v1/webhooks/provider-a",
        json=webhook_payload(
            event_id="evt-refunded",
            event_type="payment.refunded",
        ),
    )

    assert failed.status_code == 201
    assert refunded.status_code == 201
    assert repository.payments["pay-001"].status == PaymentStatus.REFUNDED


@pytest.mark.parametrize(
    "changed",
    [
        {"event_type": "payment.failed"},
        {"payload": {"provider_reference": "pay-001", "changed": True}},
    ],
)
def test_event_id_collision_with_different_content_returns_409(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
    changed: dict[str, object],
) -> None:
    client, repository, _, _ = webhook_context
    original = webhook_payload()
    assert client.post("/api/v1/webhooks/provider-a", json=original).status_code == 201

    response = client.post("/api/v1/webhooks/provider-a", json=original | changed)

    assert response.status_code == 409
    assert len(repository.webhook_events) == 1


def test_global_event_id_collision_with_different_provider_returns_409(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, repository, _, _ = webhook_context
    assert (
        client.post("/api/v1/webhooks/provider-a", json=webhook_payload()).status_code
        == 201
    )

    response = client.post("/api/v1/webhooks/provider-b", json=webhook_payload())

    assert response.status_code == 409
    assert len(repository.webhook_events) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"event_type": "provider.ping", "payload": {}},
        {"provider_event_id": "evt", "payload": {}},
        {"provider_event_id": "evt", "event_type": "provider.ping"},
        {"provider_event_id": "", "event_type": "provider.ping", "payload": {}},
        {"provider_event_id": "evt", "event_type": "", "payload": {}},
        {"provider_event_id": "evt", "event_type": "provider.ping", "payload": []},
        {
            "provider_event_id": "evt",
            "event_type": "provider.ping",
            "payload": {},
            "unknown": True,
        },
    ],
)
def test_webhook_receiver_rejects_invalid_envelope(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
    payload: dict[str, object],
) -> None:
    client, repository, _, _ = webhook_context

    response = client.post("/api/v1/webhooks/provider-a", json=payload)

    assert response.status_code == 422
    assert repository.webhook_events == {}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"provider_reference": None},
        {"provider_reference": ""},
        {"provider_reference": 123},
    ],
)
def test_recognized_event_requires_valid_provider_reference(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
    payload: dict[str, object],
) -> None:
    client, repository, _, _ = webhook_context

    response = client.post(
        "/api/v1/webhooks/provider-a",
        json={
            "provider_event_id": "evt-invalid-reference",
            "event_type": "payment.approved",
            "payload": payload,
        },
    )

    assert response.status_code == 422
    assert repository.webhook_events == {}


def test_unknown_event_is_preserved_without_payment_effect(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, repository, _, _ = webhook_context
    status_before = repository.payments["pay-001"].status
    arbitrary_payload = {
        "nested": {"attempt": 1, "values": [True, None, "kept"]},
        "amount": "999999.99",
    }

    response = client.post(
        "/api/v1/webhooks/provider-a",
        json={
            "provider_event_id": "evt-unknown",
            "event_type": "provider.unhandled",
            "payload": arbitrary_payload,
        },
    )

    assert response.status_code == 201
    assert response.json()["payload"] == arbitrary_payload
    assert response.json()["processed_at"] is None
    assert repository.payments["pay-001"].status == status_before


def test_missing_payment_is_not_created_and_event_remains_auditable(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, repository, _, _ = webhook_context

    response = client.post(
        "/api/v1/webhooks/provider-a",
        json=webhook_payload(provider_reference="missing-payment"),
    )

    assert response.status_code == 404
    assert len(repository.payments) == 2
    assert len(repository.webhook_events) == 1
    event = next(iter(repository.webhook_events.values()))
    assert event.processed_at is None


def test_wrong_provider_does_not_change_payment_and_event_is_auditable(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, repository, _, _ = webhook_context

    response = client.post("/api/v1/webhooks/provider-b", json=webhook_payload())

    assert response.status_code == 409
    assert repository.payments["pay-001"].status == PaymentStatus.PENDING
    assert len(repository.webhook_events) == 1
    assert next(iter(repository.webhook_events.values())).processed_at is None


def test_invalid_transition_does_not_change_payment_and_event_is_auditable(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, repository, _, _ = webhook_context

    response = client.post(
        "/api/v1/webhooks/provider-a",
        json=webhook_payload(
            provider_reference="pay-failed",
            event_type="payment.approved",
        ),
    )

    assert response.status_code == 409
    assert repository.payments["pay-failed"].status == PaymentStatus.FAILED
    assert len(repository.webhook_events) == 1
    assert next(iter(repository.webhook_events.values())).processed_at is None


def test_rejected_event_redelivery_is_idempotent_and_not_reprocessed(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, repository, _, _ = webhook_context
    payload = webhook_payload(provider_reference="missing-payment")

    first = client.post("/api/v1/webhooks/provider-a", json=payload)
    second = client.post("/api/v1/webhooks/provider-a", json=payload)

    assert first.status_code == 404
    assert second.status_code == 200
    assert second.json()["processed_at"] is None
    assert len(repository.webhook_events) == 1
    assert repository.process_calls == 0


def test_persistence_failure_rolls_back_and_next_event_succeeds(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, repository, _, _ = webhook_context
    repository.fail_next_create = True

    failed = client.post(
        "/api/v1/webhooks/provider-a",
        json={
            "provider_event_id": "evt-failed-write",
            "event_type": "provider.ping",
            "payload": {},
        },
    )
    recovered = client.post(
        "/api/v1/webhooks/provider-a",
        json={
            "provider_event_id": "evt-recovery",
            "event_type": "provider.ping",
            "payload": {},
        },
    )

    assert failed.status_code == 409
    assert recovered.status_code == 201
    assert len(repository.webhook_events) == 1


def test_admin_endpoints_require_bearer_authentication() -> None:
    with TestClient(app) as client:
        list_response = client.get("/api/v1/webhook-events")
        get_response = client.get("/api/v1/webhook-events/1")

    assert list_response.status_code == 401
    assert get_response.status_code == 401


def test_admin_list_and_get_support_audit_pagination(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
) -> None:
    client, _, _, _ = webhook_context
    empty = client.get("/api/v1/webhook-events")
    assert empty.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "pages": 0,
    }
    for index in range(1, 6):
        response = client.post(
            "/api/v1/webhooks/provider-a",
            json={
                "provider_event_id": f"evt-{index}",
                "event_type": "provider.ping",
                "payload": {"index": index},
            },
        )
        assert response.status_code == 201

    page = client.get("/api/v1/webhook-events?page=2&page_size=2")
    existing = client.get("/api/v1/webhook-events/3")
    missing = client.get("/api/v1/webhook-events/999")

    assert page.status_code == 200
    assert [event["id"] for event in page.json()["items"]] == [3, 4]
    assert page.json() | {"items": []} == {
        "items": [],
        "total": 5,
        "page": 2,
        "page_size": 2,
        "pages": 3,
    }
    assert existing.status_code == 200
    assert existing.json()["id"] == 3
    assert missing.status_code == 404


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101"])
def test_admin_list_validates_pagination(
    webhook_context: tuple[TestClient, InMemoryWebhookEventRepository, Order, Product],
    query: str,
) -> None:
    client, _, _, _ = webhook_context

    response = client.get(f"/api/v1/webhook-events?{query}")

    assert response.status_code == 422


def test_webhook_openapi_separates_public_receiver_from_authenticated_audit() -> None:
    schema = app.openapi()
    receiver = schema["paths"]["/api/v1/webhooks/{provider}"]
    collection = schema["paths"]["/api/v1/webhook-events"]
    resource = schema["paths"]["/api/v1/webhook-events/{webhook_event_id}"]

    assert set(receiver) == {"post"}
    assert "security" not in receiver["post"]
    assert {"200", "201", "404", "409", "422"} <= set(receiver["post"]["responses"])
    assert set(collection) == {"get"}
    assert set(resource) == {"get"}
    for operation in [*collection.values(), *resource.values()]:
        assert operation["security"] == [{"OAuth2PasswordBearer": []}]
    assert {
        "WebhookEventCreate",
        "WebhookEventListResponse",
        "WebhookEventResponse",
    } <= set(schema["components"]["schemas"])
