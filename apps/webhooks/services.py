"""
Webhook ingestion + processing service layer.

Responsibilities:
  * verify signatures (Stripe / Paystack; demo-aware)
  * enforce idempotency (duplicate events are stored but ignored)
  * normalize into the internal event shape
  * apply the event to payment intents / transactions
  * fan out to CRMs
  * drive retry/backoff for failed processing
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from django.utils import timezone

from apps.audit.services import record_audit
from apps.common.retry import DeliveryStatus
from apps.crm.services import deliver_crm, enqueue_crm_deliveries
from apps.payments.models import (
    PaymentIntent,
    PaymentStatus,
    PaymentTransaction,
    TransactionStatus,
    generate_reference,
)
from apps.processors.models import PaymentProcessor, ProcessorCode

from . import signatures
from .models import WebhookDeliveryAttempt, WebhookEvent
from .normalize import normalize_event


class WebhookError(Exception):
    """Raised for unrecoverable ingestion problems (bad processor, etc.)."""


@dataclass
class IngestResult:
    event: WebhookEvent | None
    accepted: bool
    duplicate: bool
    verified: bool
    note: str
    http_status: int

    def as_response(self) -> dict:
        return {
            "received": True,
            "accepted": self.accepted,
            "duplicate": self.duplicate,
            "verified": self.verified,
            "note": self.note,
            "event_id": self.event.event_id if self.event else None,
        }


def _verify(processor_code: str, raw_body: str, headers: dict) -> signatures.VerificationResult:
    if processor_code == ProcessorCode.STRIPE:
        return signatures.verify_stripe(raw_body, headers.get("stripe-signature", ""))
    if processor_code == ProcessorCode.PAYSTACK:
        return signatures.verify_paystack(raw_body, headers.get("x-paystack-signature", ""))
    # Manual/internal demo processor: accepted, marked as demo.
    return signatures.VerificationResult(True, "internal demo processor", demo=True)


def ingest_webhook(
    *,
    processor_code: str,
    raw_body: str,
    headers: dict,
    actor=None,
    ip_address: str | None = None,
) -> IngestResult:
    """Ingest one raw webhook. Always stores the payload for audit."""
    try:
        processor = PaymentProcessor.objects.get(code=processor_code)
    except PaymentProcessor.DoesNotExist as exc:
        raise WebhookError(f"Unknown processor '{processor_code}'.") from exc

    verification = _verify(processor_code, raw_body, headers)

    try:
        data = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        data = {}

    normalized = normalize_event(processor_code, data)
    event_id = normalized.get("event_id") or f"evt_{generate_reference('x')}"
    event_type = normalized.get("event_type", "")

    record_audit(
        "webhook_received",
        f"{processor_code} webhook {event_id} received",
        actor=actor,
        entity_type="WebhookEvent",
        entity_id=event_id,
        ip_address=ip_address,
    )

    # --- rejected signature: store + stop --------------------------------
    if verification.rejected:
        event = WebhookEvent.objects.create(
            processor=processor,
            event_id=event_id,
            event_type=event_type,
            signature_verified=False,
            verification_note=verification.note,
            raw_payload=raw_body,
            headers=headers,
            normalized={},
            status=DeliveryStatus.PERMANENTLY_FAILED,
            processed_at=timezone.now(),
        )
        record_audit(
            "webhook_rejected",
            f"{processor_code} webhook {event_id} rejected: {verification.note}",
            actor=actor,
            entity_type="WebhookEvent",
            entity_id=event_id,
            ip_address=ip_address,
        )
        return IngestResult(
            event=event, accepted=False, duplicate=False, verified=False,
            note=verification.note, http_status=400,
        )

    record_audit(
        "webhook_verified",
        f"{processor_code} webhook {event_id}: {verification.note}",
        actor=actor,
        entity_type="WebhookEvent",
        entity_id=event_id,
        ip_address=ip_address,
    )

    # --- idempotency: has a non-duplicate original already arrived? -------
    original = WebhookEvent.objects.filter(
        processor=processor, event_id=event_id, is_duplicate=False
    ).first()

    if original is not None:
        duplicate = WebhookEvent.objects.create(
            processor=processor,
            event_id=event_id,
            event_type=event_type,
            signature_verified=True,
            verification_note=verification.note,
            is_duplicate=True,
            raw_payload=raw_body,
            headers=headers,
            normalized=normalized,
            status=DeliveryStatus.SUCCESS,  # nothing to do; ignored
            processed_at=timezone.now(),
        )
        record_audit(
            "webhook_duplicate",
            f"Duplicate {processor_code} webhook {event_id} ignored",
            actor=actor,
            entity_type="WebhookEvent",
            entity_id=event_id,
            ip_address=ip_address,
        )
        return IngestResult(
            event=duplicate, accepted=True, duplicate=True, verified=True,
            note="duplicate event ignored", http_status=200,
        )

    # --- first occurrence: store + process -------------------------------
    event = WebhookEvent.objects.create(
        processor=processor,
        event_id=event_id,
        event_type=event_type,
        signature_verified=True,
        verification_note=verification.note,
        raw_payload=raw_body,
        headers=headers,
        normalized=normalized,
        status=DeliveryStatus.PENDING,
    )
    process_webhook_event(event, actor=actor)
    event.refresh_from_db()
    accepted = event.status != DeliveryStatus.PERMANENTLY_FAILED
    return IngestResult(
        event=event, accepted=accepted, duplicate=False, verified=True,
        note=verification.note, http_status=200,
    )


def _apply_normalized_event(event: WebhookEvent) -> None:
    """Apply a normalized event to payment intents/transactions + CRM."""
    n = event.normalized
    reference = n.get("reference", "")
    outcome = n.get("outcome", "pending")

    intent = (
        PaymentIntent.objects.filter(reference=reference).first()
        if reference
        else None
    )

    if intent is not None and outcome in {"succeeded", "failed"}:
        intent.status = (
            PaymentStatus.SUCCEEDED if outcome == "succeeded" else PaymentStatus.FAILED
        )
        intent.save(update_fields=["status", "updated_at"])
        PaymentTransaction.objects.create(
            intent=intent,
            processor=event.processor,
            reference=n.get("external_id") or generate_reference("txn"),
            external_id=n.get("external_id", ""),
            amount=intent.amount,
            currency=intent.currency,
            status=(
                TransactionStatus.SUCCEEDED
                if outcome == "succeeded"
                else TransactionStatus.FAILED
            ),
            raw_response=n,
        )

    # Fan out to CRMs and attempt each once (retries handled by the job runner).
    deliveries = enqueue_crm_deliveries(
        payment_intent=intent,
        event_type=n.get("event_type", ""),
        source_event_id=event.event_id,
        payload=n,
    )
    for delivery in deliveries:
        if delivery.status in {DeliveryStatus.PENDING, DeliveryStatus.FAILED}:
            deliver_crm(delivery)


def process_webhook_event(event: WebhookEvent, *, actor=None) -> bool:
    """Process one webhook event, updating retry state on failure."""
    if event.is_duplicate:
        return True
    event.mark_processing()
    attempt_number = event.retry_count + 1
    try:
        _apply_normalized_event(event)
    except Exception as exc:  # noqa: BLE001 - capture for retry/backoff
        event.mark_failure(str(exc))
        WebhookDeliveryAttempt.objects.create(
            event=event,
            attempt_number=attempt_number,
            result=WebhookDeliveryAttempt.Result.FAILED,
            detail=str(exc),
        )
        record_audit(
            "webhook_processed",
            f"Webhook {event.event_id} processing failed ({event.status})",
            actor=actor,
            entity_type="WebhookEvent",
            entity_id=event.event_id,
            metadata={"attempt": attempt_number, "error": str(exc)},
        )
        return False

    event.mark_success()
    event.processed_at = timezone.now()
    event.save(update_fields=["processed_at"])
    WebhookDeliveryAttempt.objects.create(
        event=event,
        attempt_number=attempt_number,
        result=WebhookDeliveryAttempt.Result.SUCCESS,
        detail="processed",
    )
    record_audit(
        "webhook_processed",
        f"Webhook {event.event_id} processed successfully",
        actor=actor,
        entity_type="WebhookEvent",
        entity_id=event.event_id,
        metadata={"attempt": attempt_number},
    )
    return True


def retry_webhook_event(event: WebhookEvent, *, actor=None) -> bool:
    """Operator-triggered manual retry of a failed webhook event."""
    event.reset_for_manual_retry()
    record_audit(
        "webhook_retried",
        f"Manual retry of webhook {event.event_id}",
        actor=actor,
        entity_type="WebhookEvent",
        entity_id=event.event_id,
    )
    return process_webhook_event(event, actor=actor)


def process_due_webhook_events(*, limit: int = 100) -> dict[str, int]:
    """Process every webhook event whose backoff window has elapsed."""
    candidates = WebhookEvent.objects.filter(
        is_duplicate=False,
        status__in=[DeliveryStatus.PENDING, DeliveryStatus.FAILED],
    ).order_by("next_retry_at")[:limit]
    due = [e for e in candidates if e.is_due]

    succeeded = failed = 0
    for event in due:
        if process_webhook_event(event):
            succeeded += 1
        else:
            failed += 1
    return {"processed": len(due), "succeeded": succeeded, "failed": failed}
