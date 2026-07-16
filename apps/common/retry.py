"""
Shared retry / exponential-backoff primitives.

Both webhook processing jobs and CRM deliveries are "retryable jobs": they
move through the same lifecycle and use the same exponential backoff schedule.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed (will retry)"
    PERMANENTLY_FAILED = "permanently_failed", "Permanently failed"


def compute_backoff_seconds(retry_count: int) -> int:
    """Exponential backoff with a ceiling.

    delay = base * 2**retry_count, capped at RETRY_MAX_BACKOFF_SECONDS.
    retry_count is the number of attempts already made (0-based).
    """
    base = settings.RETRY_BASE_SECONDS
    cap = settings.RETRY_MAX_BACKOFF_SECONDS
    delay = base * (2 ** max(retry_count, 0))
    return int(min(delay, cap))


def next_retry_at(retry_count: int):
    return timezone.now() + timedelta(seconds=compute_backoff_seconds(retry_count))


class RetryableJob(models.Model):
    """Abstract base carrying retry/backoff state for a deliverable job."""

    status = models.CharField(
        max_length=20,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
    )
    retry_count = models.PositiveIntegerField(default=0)
    max_retries = models.PositiveIntegerField(default=settings.RETRY_MAX_ATTEMPTS)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    # --- lifecycle transitions -------------------------------------------
    def mark_processing(self) -> None:
        self.status = DeliveryStatus.PROCESSING
        self.last_attempt_at = timezone.now()
        self.save(update_fields=["status", "last_attempt_at"])

    def mark_success(self) -> None:
        self.status = DeliveryStatus.SUCCESS
        self.next_retry_at = None
        self.last_error = ""
        self.save(update_fields=["status", "next_retry_at", "last_error"])

    def mark_failure(self, error: str) -> None:
        """Record a failed attempt and schedule the next retry (or give up)."""
        self.retry_count += 1
        self.last_error = (error or "")[:2000]
        self.last_attempt_at = timezone.now()
        if self.retry_count >= self.max_retries:
            self.status = DeliveryStatus.PERMANENTLY_FAILED
            self.next_retry_at = None
        else:
            self.status = DeliveryStatus.FAILED
            self.next_retry_at = next_retry_at(self.retry_count)
        self.save(
            update_fields=[
                "retry_count", "last_error", "last_attempt_at",
                "status", "next_retry_at",
            ]
        )

    def reset_for_manual_retry(self) -> None:
        """Operator-triggered retry: make the job eligible immediately."""
        if self.status in {DeliveryStatus.SUCCESS, DeliveryStatus.PROCESSING}:
            return
        self.status = DeliveryStatus.PENDING
        self.next_retry_at = timezone.now()
        self.save(update_fields=["status", "next_retry_at"])

    @property
    def is_due(self) -> bool:
        if self.status not in {DeliveryStatus.PENDING, DeliveryStatus.FAILED}:
            return False
        return self.next_retry_at is None or self.next_retry_at <= timezone.now()
