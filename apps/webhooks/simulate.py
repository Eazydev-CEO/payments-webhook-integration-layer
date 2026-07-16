"""
Local webhook simulation.

Builds realistically-shaped Stripe / Paystack / manual payloads and signs them
with the configured webhook secret (if any) so they pass verification. Used by
the dashboard "simulate webhook" action and by the test suite.
"""
from __future__ import annotations

import json
import secrets
import time

from django.conf import settings

from apps.processors.models import ProcessorCode

from . import signatures


def _amount_minor(amount) -> int:
    return int(round(float(amount) * 100))


def build_stripe_event(*, reference: str, amount, currency: str, email: str, succeeded: bool):
    event_type = "payment_intent.succeeded" if succeeded else "payment_intent.payment_failed"
    body = json.dumps(
        {
            "id": f"evt_{secrets.token_hex(8)}",
            "type": event_type,
            "data": {
                "object": {
                    "id": f"pi_{secrets.token_hex(8)}",
                    "amount": _amount_minor(amount),
                    "currency": currency.lower(),
                    "receipt_email": email,
                    "metadata": {"reference": reference},
                }
            },
        }
    )
    timestamp = str(int(time.time()))
    headers = {}
    secret = settings.STRIPE_WEBHOOK_SECRET
    if secret:
        sig = signatures.compute_stripe_signature(body, timestamp, secret)
        headers["stripe-signature"] = f"t={timestamp},v1={sig}"
    else:
        headers["stripe-signature"] = f"t={timestamp},v1=demo"
    return body, headers


def build_paystack_event(*, reference: str, amount, currency: str, email: str, succeeded: bool):
    event_type = "charge.success" if succeeded else "charge.failed"
    body = json.dumps(
        {
            "event": event_type,
            "data": {
                "id": secrets.randbelow(9_000_000) + 1_000_000,
                "reference": reference,
                "amount": _amount_minor(amount),
                "currency": currency.upper(),
                "status": "success" if succeeded else "failed",
                "customer": {"email": email},
            },
        }
    )
    headers = {}
    secret = settings.PAYSTACK_SECRET_KEY
    if secret:
        headers["x-paystack-signature"] = signatures.compute_paystack_signature(body, secret)
    else:
        headers["x-paystack-signature"] = "demo"
    return body, headers


def build_manual_event(*, reference: str, amount, currency: str, email: str, succeeded: bool):
    body = json.dumps(
        {
            "event_id": f"man_{secrets.token_hex(8)}",
            "type": "payment.succeeded" if succeeded else "payment.failed",
            "reference": reference,
            "external_id": f"demo_{secrets.token_hex(6)}",
            "amount": str(amount),
            "currency": currency.upper(),
            "customer_email": email,
        }
    )
    return body, {}


BUILDERS = {
    ProcessorCode.STRIPE: build_stripe_event,
    ProcessorCode.PAYSTACK: build_paystack_event,
    ProcessorCode.MANUAL: build_manual_event,
}


def build_signed_event(processor_code: str, **kwargs):
    """Return (raw_body, headers) for the given processor."""
    builder = BUILDERS.get(processor_code, build_manual_event)
    return builder(**kwargs)
