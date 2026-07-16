"""
Payment intent service layer.

All creation flows are idempotent: repeating a request with the same
``idempotency_key`` returns the original intent instead of creating a
duplicate. This mirrors how Stripe/Paystack handle idempotent requests.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from apps.audit.services import record_audit
from apps.processors.models import PaymentProcessor, ProcessorCode

from .models import (
    PaymentIntent,
    PaymentStatus,
    PaymentTransaction,
    TransactionStatus,
    generate_reference,
)


class PaymentError(Exception):
    """Raised for invalid payment operations (mapped to HTTP 400)."""


@dataclass
class IntentResult:
    intent: PaymentIntent
    created: bool  # False => idempotent replay of an existing intent


def _resolve_processor(processor_code: str) -> PaymentProcessor:
    try:
        return PaymentProcessor.objects.get(code=processor_code, is_active=True)
    except PaymentProcessor.DoesNotExist as exc:
        raise PaymentError(f"Unknown or inactive processor '{processor_code}'.") from exc


def create_payment_intent(
    *,
    idempotency_key: str,
    processor_code: str,
    customer_name: str,
    customer_email: str,
    amount,
    currency: str = "USD",
    metadata: dict | None = None,
    actor=None,
    ip_address: str | None = None,
) -> IntentResult:
    """Create a payment intent idempotently.

    Returns the existing intent (``created=False``) when the idempotency key
    has already been used, so callers always get a consistent response.
    """
    if not idempotency_key:
        raise PaymentError("An idempotency key is required.")

    # Fast path: idempotent replay.
    existing = PaymentIntent.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        record_audit(
            "intent_duplicate",
            f"Idempotent replay for key {idempotency_key} -> {existing.reference}",
            actor=actor,
            entity_type="PaymentIntent",
            entity_id=existing.reference,
            ip_address=ip_address,
        )
        return IntentResult(intent=existing, created=False)

    try:
        amount_dec = Decimal(str(amount))
    except (InvalidOperation, TypeError) as exc:
        raise PaymentError("Amount must be a valid decimal number.") from exc
    if amount_dec <= 0:
        raise PaymentError("Amount must be greater than zero.")

    processor = _resolve_processor(processor_code)

    try:
        with transaction.atomic():
            intent = PaymentIntent.objects.create(
                idempotency_key=idempotency_key,
                processor=processor,
                customer_name=customer_name,
                customer_email=customer_email,
                amount=amount_dec,
                currency=currency.upper()[:3],
                metadata=metadata or {},
                status=PaymentStatus.CREATED,
            )
    except IntegrityError:
        # Concurrent request won the race on the unique key: return theirs.
        existing = PaymentIntent.objects.get(idempotency_key=idempotency_key)
        return IntentResult(intent=existing, created=False)

    record_audit(
        "intent_created",
        f"Created intent {intent.reference} for {intent.amount} {intent.currency}",
        actor=actor,
        entity_type="PaymentIntent",
        entity_id=intent.reference,
        metadata={"processor": processor.code, "amount": str(intent.amount)},
        ip_address=ip_address,
    )
    return IntentResult(intent=intent, created=True)


def mark_demo_payment(
    *,
    intent: PaymentIntent,
    outcome: str,
    actor=None,
    ip_address: str | None = None,
) -> PaymentTransaction:
    """Mark a demo/manual intent as succeeded or failed and record a transaction.

    Only allowed for the manual processor (or any processor while in demo mode)
    so the operator can simulate settlement without a live charge.
    """
    if outcome not in {"success", "failed"}:
        raise PaymentError("Outcome must be 'success' or 'failed'.")
    if intent.is_terminal:
        raise PaymentError(f"Intent {intent.reference} is already {intent.status}.")

    succeeded = outcome == "success"
    with transaction.atomic():
        intent.status = PaymentStatus.SUCCEEDED if succeeded else PaymentStatus.FAILED
        intent.save(update_fields=["status", "updated_at"])
        txn = PaymentTransaction.objects.create(
            intent=intent,
            processor=intent.processor,
            reference=generate_reference("txn"),
            external_id=f"demo_{generate_reference('ch')}",
            amount=intent.amount,
            currency=intent.currency,
            status=(
                TransactionStatus.SUCCEEDED if succeeded else TransactionStatus.FAILED
            ),
            raw_response={"demo": True, "outcome": outcome},
        )

    record_audit(
        "payment_marked",
        f"Demo intent {intent.reference} marked {outcome}",
        actor=actor,
        entity_type="PaymentIntent",
        entity_id=intent.reference,
        metadata={"outcome": outcome, "transaction": txn.reference},
        ip_address=ip_address,
    )
    return txn
