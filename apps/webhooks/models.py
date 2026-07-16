from __future__ import annotations

from django.db import models

from apps.common.retry import RetryableJob
from apps.processors.models import PaymentProcessor


class WebhookEvent(RetryableJob):
    """A received webhook, stored verbatim for audit + idempotent processing.

    Inherits retry/backoff state (status, retry_count, next_retry_at, ...) so
    processing failures are retried with exponential backoff. Idempotency is
    enforced in the service layer, not by a database constraint: a re-delivered
    (processor, event_id) is still stored — flagged ``is_duplicate`` — so that
    duplicates stay visible for audit rather than being rejected at the DB
    boundary. See the ``Meta`` note below and ``apps/webhooks/services.py``.
    """

    processor = models.ForeignKey(
        PaymentProcessor, on_delete=models.PROTECT, related_name="webhook_events"
    )
    # Processor-supplied unique event id (Stripe evt_..., Paystack, or demo).
    event_id = models.CharField(max_length=120)
    event_type = models.CharField(max_length=80, blank=True)

    # Signature verification outcome.
    signature_verified = models.BooleanField(default=False)
    verification_note = models.CharField(max_length=255, blank=True)

    # Idempotency: a re-delivered event is stored but flagged and not reprocessed.
    is_duplicate = models.BooleanField(default=False)

    # Raw material kept for audit / replay.
    raw_payload = models.TextField(blank=True)
    headers = models.JSONField(default=dict, blank=True)

    # Normalized internal representation of the event.
    normalized = models.JSONField(default=dict, blank=True)

    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-received_at",)
        # No hard unique constraint: duplicate re-deliveries are *stored* (for
        # audit) but flagged is_duplicate and never reprocessed. Idempotency is
        # enforced in the service layer against the first non-duplicate event.
        indexes = [
            models.Index(fields=["processor", "event_id"]),
            models.Index(fields=["status", "next_retry_at"]),
            models.Index(fields=["signature_verified", "received_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.processor.code}:{self.event_id} ({self.event_type})"


class WebhookDeliveryAttempt(models.Model):
    """One processing attempt for a webhook event (audit trail for retries)."""

    class Result(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    event = models.ForeignKey(
        WebhookEvent, on_delete=models.CASCADE, related_name="attempts"
    )
    attempt_number = models.PositiveIntegerField()
    result = models.CharField(max_length=10, choices=Result.choices)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"attempt {self.attempt_number} · {self.result}"
