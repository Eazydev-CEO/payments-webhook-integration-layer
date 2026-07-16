from __future__ import annotations

import secrets
from decimal import Decimal

from django.db import models

from apps.processors.models import PaymentProcessor


def generate_reference(prefix: str = "pi") -> str:
    """Human-friendly unique reference, e.g. ``pi_9f3a1c2b8d4e``."""
    return f"{prefix}_{secrets.token_hex(6)}"


class PaymentStatus(models.TextChoices):
    CREATED = "created", "Created"
    PROCESSING = "processing", "Processing"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"


class PaymentIntent(models.Model):
    """A normalized intent to collect a payment via one processor."""

    reference = models.CharField(max_length=40, unique=True, default=generate_reference)
    # Idempotency key supplied by the caller; guarantees no duplicate intents.
    idempotency_key = models.CharField(max_length=80, unique=True, db_index=True)

    processor = models.ForeignKey(
        PaymentProcessor, on_delete=models.PROTECT, related_name="intents"
    )
    customer_name = models.CharField(max_length=120)
    customer_email = models.EmailField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.CREATED
    )
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["processor", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.reference} · {self.amount} {self.currency} ({self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELED,
        }


class TransactionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class PaymentTransaction(models.Model):
    """A concrete money movement attached to an intent (from a webhook/charge)."""

    intent = models.ForeignKey(
        PaymentIntent, on_delete=models.CASCADE, related_name="transactions"
    )
    processor = models.ForeignKey(
        PaymentProcessor, on_delete=models.PROTECT, related_name="transactions"
    )
    reference = models.CharField(max_length=64, db_index=True)
    external_id = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(
        max_length=20, choices=TransactionStatus.choices, default=TransactionStatus.PENDING
    )
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self) -> str:
        return f"{self.reference} · {self.amount} {self.currency} ({self.status})"

    @property
    def amount_decimal(self) -> Decimal:
        return Decimal(self.amount)
