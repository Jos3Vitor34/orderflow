from dataclasses import dataclass
from typing import Protocol

import stripe
from pydantic import SecretStr
from stripe import StripeClient


class StripeGatewayError(Exception):
    """Base error for sanitized Stripe adapter failures."""


class StripeConfigurationError(StripeGatewayError):
    """Raised when Stripe Test Mode configuration is absent or unsafe."""


class StripeAuthenticationError(StripeGatewayError):
    """Raised when Stripe rejects the configured credentials or permissions."""


class StripeInvalidRequestError(StripeGatewayError):
    """Raised when Stripe rejects server-generated PaymentIntent parameters."""


class StripeIdempotencyError(StripeGatewayError):
    """Raised when Stripe rejects reuse of an idempotency key."""


class StripeTemporaryError(StripeGatewayError):
    """Raised when the Stripe outcome is temporary or indeterminate."""


class StripeProviderError(StripeGatewayError):
    """Raised for an unexpected sanitized Stripe provider failure."""


@dataclass(frozen=True)
class StripePaymentIntent:
    id: str
    status: str
    amount: int
    currency: str


class StripeGateway(Protocol):
    def create_payment_intent(
        self,
        *,
        amount: int,
        currency: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> StripePaymentIntent: ...


class StripePaymentIntentGateway:
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

    def create_payment_intent(
        self,
        *,
        amount: int,
        currency: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> StripePaymentIntent:
        try:
            payment_intent = self._get_client().v1.payment_intents.create(
                {
                    "amount": amount,
                    "currency": currency,
                    "automatic_payment_methods": {"enabled": True},
                    "metadata": metadata,
                },
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

        intent_id = payment_intent.id
        intent_status = payment_intent.status
        intent_amount = payment_intent.amount
        intent_currency = payment_intent.currency
        if (
            not isinstance(intent_id, str)
            or not intent_id
            or len(intent_id) > 255
            or not isinstance(intent_status, str)
            or not intent_status
            or not isinstance(intent_amount, int)
            or not isinstance(intent_currency, str)
            or not intent_currency
        ):
            raise StripeProviderError
        return StripePaymentIntent(
            id=intent_id,
            status=intent_status,
            amount=intent_amount,
            currency=intent_currency,
        )
