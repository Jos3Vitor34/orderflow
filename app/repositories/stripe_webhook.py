from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.payment import Payment, PaymentStatus
from app.models.refund import Refund, RefundStatus
from app.models.webhook_event import WebhookEvent
from app.repositories.webhook_event import WebhookEventRepository, raise_write_error


class StripeWebhookRepository(WebhookEventRepository):
    def get_refund_by_provider_id(self, provider_refund_id: str) -> Refund | None:
        return self._session.scalar(
            select(Refund).where(Refund.provider_refund_id == provider_refund_id)
        )

    def get_refund_by_id(self, refund_id: int) -> Refund | None:
        return self._session.get(Refund, refund_id)

    def get_refund_by_provider_id_for_update(
        self,
        provider_refund_id: str,
    ) -> Refund | None:
        return self._session.scalar(
            select(Refund)
            .where(Refund.provider_refund_id == provider_refund_id)
            .with_for_update()
        )

    def get_refund_by_id_for_update(self, refund_id: int) -> Refund | None:
        return self._session.scalar(
            select(Refund).where(Refund.id == refund_id).with_for_update()
        )

    def create_external_refund(
        self,
        *,
        payment_id: int,
        provider_refund_id: str,
        amount: Decimal,
        currency: str,
        status: RefundStatus,
        reason: str | None,
        failure_reason: str | None,
        provider_created_at: datetime,
        event_created_at: datetime,
    ) -> Refund:
        refund = Refund(
            payment_id=payment_id,
            provider="stripe",
            provider_refund_id=provider_refund_id,
            amount=amount,
            currency=currency,
            status=status,
            reason=reason,
            failure_reason=failure_reason,
            provider_created_at=provider_created_at,
            last_provider_event_created_at=event_created_at,
        )
        self._session.add(refund)
        return refund

    def succeeded_refund_amount(self, payment_id: int) -> Decimal:
        value = self._session.scalar(
            select(func.coalesce(func.sum(Refund.amount), 0)).where(
                Refund.payment_id == payment_id,
                Refund.status == RefundStatus.SUCCEEDED,
            )
        )
        return Decimal(value or 0)

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
        refund.provider_refund_id = provider_refund_id
        refund.status = refund_status
        refund.reason = reason
        refund.failure_reason = failure_reason
        refund.provider_created_at = provider_created_at
        refund.last_provider_event_created_at = event_created_at
        payment.status = payment_status
        webhook_event.processed_at = datetime.now(UTC)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)
        self._session.refresh(webhook_event)
        return webhook_event
