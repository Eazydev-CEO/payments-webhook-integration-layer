"""
Event normalization.

Every processor speaks a different dialect. We translate Stripe and Paystack
payloads (and our internal demo processor) into ONE internal event shape so
that everything downstream — payment updates, CRM fan-out, reconciliation —
only ever deals with a single format.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from apps.processors.models import ProcessorCode

# Internal, processor-agnostic event types.
INTERNAL_SUCCEEDED = "payment.succeeded"
INTERNAL_FAILED = "payment.failed"
INTERNAL_UPDATED = "payment.updated"

_STRIPE_TYPE_MAP = {
    "payment_intent.succeeded": INTERNAL_SUCCEEDED,
    "charge.succeeded": INTERNAL_SUCCEEDED,
    "payment_intent.payment_failed": INTERNAL_FAILED,
    "charge.failed": INTERNAL_FAILED,
}
_PAYSTACK_TYPE_MAP = {
    "charge.success": INTERNAL_SUCCEEDED,
    "charge.failed": INTERNAL_FAILED,
}


def extract_event_meta(processor_code: str, data: dict[str, Any]) -> tuple[str, str]:
    """Return (event_id, raw_event_type) from a parsed payload."""
    if processor_code == ProcessorCode.STRIPE:
        return str(data.get("id", "")), str(data.get("type", ""))
    if processor_code == ProcessorCode.PAYSTACK:
        # Paystack has no top-level event id; derive a stable one.
        obj = data.get("data", {}) or {}
        ref = obj.get("reference") or obj.get("id") or ""
        raw_type = str(data.get("event", ""))
        return f"ps_{raw_type}_{ref}", raw_type
    # Manual / internal demo processor uses our own envelope.
    return str(data.get("event_id", "")), str(data.get("type", ""))


def _minor_to_major(amount: Any) -> Decimal:
    """Stripe/Paystack express amounts in minor units (cents/kobo)."""
    try:
        return (Decimal(str(amount)) / Decimal("100")).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001 - defensive parsing
        return Decimal("0.00")


def _outcome_for(internal_type: str) -> str:
    if internal_type == INTERNAL_SUCCEEDED:
        return "succeeded"
    if internal_type == INTERNAL_FAILED:
        return "failed"
    return "pending"


def normalize_event(processor_code: str, data: dict[str, Any]) -> dict[str, Any]:
    """Translate a raw processor payload into the internal event shape."""
    event_id, raw_type = extract_event_meta(processor_code, data)

    if processor_code == ProcessorCode.STRIPE:
        obj = (data.get("data", {}) or {}).get("object", {}) or {}
        internal_type = _STRIPE_TYPE_MAP.get(raw_type, INTERNAL_UPDATED)
        reference = (obj.get("metadata", {}) or {}).get("reference", "")
        return {
            "event_id": event_id,
            "processor": processor_code,
            "event_type": internal_type,
            "raw_event_type": raw_type,
            "reference": reference,
            "external_id": obj.get("id", ""),
            "amount": str(_minor_to_major(obj.get("amount", 0))),
            "currency": str(obj.get("currency", "usd")).upper()[:3],
            "customer_email": obj.get("receipt_email") or obj.get("email", ""),
            "outcome": _outcome_for(internal_type),
        }

    if processor_code == ProcessorCode.PAYSTACK:
        obj = data.get("data", {}) or {}
        internal_type = _PAYSTACK_TYPE_MAP.get(raw_type, INTERNAL_UPDATED)
        return {
            "event_id": event_id,
            "processor": processor_code,
            "event_type": internal_type,
            "raw_event_type": raw_type,
            "reference": obj.get("reference", ""),
            "external_id": str(obj.get("id", "")),
            "amount": str(_minor_to_major(obj.get("amount", 0))),
            "currency": str(obj.get("currency", "NGN")).upper()[:3],
            "customer_email": (obj.get("customer", {}) or {}).get("email", ""),
            "outcome": _outcome_for(internal_type),
        }

    # Manual / internal demo processor: already close to internal shape.
    internal_type = raw_type if raw_type.startswith("payment.") else INTERNAL_UPDATED
    return {
        "event_id": event_id,
        "processor": processor_code,
        "event_type": internal_type,
        "raw_event_type": raw_type,
        "reference": data.get("reference", ""),
        "external_id": data.get("external_id", ""),
        "amount": str(data.get("amount", "0.00")),
        "currency": str(data.get("currency", "USD")).upper()[:3],
        "customer_email": data.get("customer_email", ""),
        "outcome": _outcome_for(internal_type),
    }
