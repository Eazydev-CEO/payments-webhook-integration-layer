"""Processor bootstrap helpers."""
from __future__ import annotations

from .models import PaymentProcessor, ProcessorCode

DEFAULT_PROCESSORS = [
    {
        "code": ProcessorCode.STRIPE,
        "name": "Stripe",
        "supports_webhooks": True,
        "config": {"dashboard": "https://dashboard.stripe.com", "region": "global"},
    },
    {
        "code": ProcessorCode.PAYSTACK,
        "name": "Paystack",
        "supports_webhooks": True,
        "config": {"dashboard": "https://dashboard.paystack.com", "region": "africa"},
    },
    {
        "code": ProcessorCode.MANUAL,
        "name": "Manual / Internal Demo",
        "supports_webhooks": True,
        "config": {"dashboard": "", "region": "internal"},
    },
]


def ensure_default_processors() -> list[PaymentProcessor]:
    """Idempotently create the three supported processors."""
    result = []
    for spec in DEFAULT_PROCESSORS:
        proc, _ = PaymentProcessor.objects.get_or_create(
            code=spec["code"],
            defaults={
                "name": spec["name"],
                "supports_webhooks": spec["supports_webhooks"],
                "config": spec["config"],
            },
        )
        result.append(proc)
    return result
