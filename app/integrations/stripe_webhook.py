from dataclasses import dataclass
from typing import Any

import stripe
from pydantic import SecretStr


class StripeWebhookVerificationError(Exception):
    """Raised when a request is not an authentic, valid Stripe event."""


class StripeWebhookConfigurationError(Exception):
    """Raised when webhook signature verification is not configured safely."""


@dataclass(frozen=True)
class VerifiedStripeEvent:
    id: str
    type: str
    payload: dict[str, Any]


class StripeWebhookVerifier:
    def __init__(self, secret: SecretStr | None) -> None:
        self._secret = secret

    def verify(self, raw_body: bytes, signature: str) -> VerifiedStripeEvent:
        if self._secret is None:
            raise StripeWebhookConfigurationError
        secret = self._secret.get_secret_value()
        if not secret.startswith("whsec_"):
            raise StripeWebhookConfigurationError

        try:
            event = stripe.Webhook.construct_event(raw_body, signature, secret)
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise StripeWebhookVerificationError from exc

        payload = event.to_dict(for_json=True)
        event_id = payload.get("id")
        event_type = payload.get("type")
        data = payload.get("data")
        if (
            not isinstance(event_id, str)
            or not event_id
            or len(event_id) > 255
            or not isinstance(event_type, str)
            or not event_type
            or len(event_type) > 120
            or payload.get("object") != "event"
            or payload.get("livemode") is not False
            or not isinstance(data, dict)
            or not isinstance(data.get("object"), dict)
        ):
            raise StripeWebhookVerificationError

        return VerifiedStripeEvent(
            id=event_id,
            type=event_type,
            payload=payload,
        )
