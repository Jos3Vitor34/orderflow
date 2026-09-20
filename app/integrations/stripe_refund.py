from dataclasses import dataclass
from typing import Protocol

import stripe
from pydantic import SecretStr
from stripe import StripeClient

from app.integrations.stripe import (
    StripeAuthenticationError,
    StripeConfigurationError,
    StripeIdempotencyError,
    StripeInvalidRequestError,
    StripeProviderError,
    StripeTemporaryError,
)


@dataclass(frozen=True)
class StripeRefund:
    id: str
    payment_intent: str
    amount: int
    currency: str
    status: str
    reason: str | None
    failure_reason: str | None
    created: int


class StripeRefundGateway(Protocol):
    def create_refund(
        self,
        *,
        payment_intent: str,
        amount: int,
        reason: str | None,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> StripeRefund: ...


class StripeRefundGatewayAdapter:
    def __init__(
        self,
        secret_key: SecretStr | None,
        *,
        client: StripeClient | None = None,
    ) -> None:
        self._secret_key = secret_key
        self._client = client

    def _get_client(self) -> StripeClient:
        if self._client is not None:
            return self._client
        if self._secret_key is None:
            raise StripeConfigurationError
        secret_key = self._secret_key.get_secret_value()
        if not secret_key.startswith(("sk_test_", "rk_test_")):
            raise StripeConfigurationError
        self._client = StripeClient(secret_key, max_network_retries=0)
        return self._client

    def create_refund(
        self,
        *,
        payment_intent: str,
        amount: int,
        reason: str | None,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> StripeRefund:
        params: dict[str, object] = {
            "payment_intent": payment_intent,
            "amount": amount,
            "metadata": metadata,
        }
        if reason is not None:
            params["reason"] = reason
        try:
            refund = self._get_client().v1.refunds.create(
                params,
                options={"idempotency_key": idempotency_key},
            )
        except StripeConfigurationError:
            raise
        except (stripe.AuthenticationError, stripe.PermissionError) as exc:
            raise StripeAuthenticationError from exc
        except stripe.InvalidRequestError as exc:
            raise StripeInvalidRequestError from exc
        except stripe.IdempotencyError as exc:
            raise StripeIdempotencyError from exc
        except (
            stripe.APIConnectionError,
            stripe.APIError,
            stripe.RateLimitError,
        ) as exc:
            raise StripeTemporaryError from exc
        except stripe.StripeError as exc:
            raise StripeProviderError from exc

        refund_id = refund.id
        refund_payment_intent = refund.payment_intent
        amount_value = refund.amount
        currency = refund.currency
        refund_status = refund.status
        created = refund.created
        if (
            not isinstance(refund_id, str)
            or not refund_id
            or len(refund_id) > 255
            or not isinstance(refund_payment_intent, str)
            or not refund_payment_intent
            or not isinstance(amount_value, int)
            or isinstance(amount_value, bool)
            or not isinstance(currency, str)
            or not currency
            or not isinstance(refund_status, str)
            or not refund_status
            or not isinstance(created, int)
        ):
            raise StripeProviderError
        reason_value = refund.reason
        failure_reason = refund.failure_reason
        if reason_value is not None and not isinstance(reason_value, str):
            raise StripeProviderError
        if failure_reason is not None and not isinstance(failure_reason, str):
            raise StripeProviderError
        return StripeRefund(
            id=refund_id,
            payment_intent=refund_payment_intent,
            amount=amount_value,
            currency=currency,
            status=refund_status,
            reason=reason_value,
            failure_reason=failure_reason,
            created=created,
        )
