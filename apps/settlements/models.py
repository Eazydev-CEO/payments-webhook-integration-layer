from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.payments.models import PaymentIntent
from apps.processors.models import PaymentProcessor


class SettlementBatch(models.Model):
    """An imported settlement statement from a processor, reconciled against
    our internal transaction records."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RECONCILED = "reconciled", "Reconciled"

    processor = models.ForeignKey(
        PaymentProcessor, on_delete=models.PROTECT, related_name="settlement_batches"
    )
    reference = models.CharField(max_length=60, unique=True)
    statement_date = models.DateField()
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")

    expected_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    received_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    difference = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    source_filename = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="settlement_batches",
    )
    summary = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-statement_date", "-created_at")

    def __str__(self) -> str:
        return f"{self.reference} · {self.processor.code} · {self.statement_date}"

    @property
    def is_balanced(self) -> bool:
        return self.difference == Decimal("0.00")

    def count(self, match_status: str) -> int:
        return self.items.filter(match_status=match_status).count()

    @property
    def mismatch_count(self) -> int:
        return self.items.exclude(match_status=MatchStatus.MATCHED).count()


class MatchStatus(models.TextChoices):
    MATCHED = "matched", "Matched"
    AMOUNT_MISMATCH = "amount_mismatch", "Amount mismatch"
    CURRENCY_MISMATCH = "currency_mismatch", "Currency mismatch"
    MISSING = "missing", "Missing from settlement"
    UNKNOWN = "unknown", "Unknown settlement record"


class SettlementItem(models.Model):
    """A single line in a settlement statement (or a synthesized 'missing' row)."""

    batch = models.ForeignKey(
        SettlementBatch, on_delete=models.CASCADE, related_name="items"
    )
    external_reference = models.CharField(max_length=64, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    reported_status = models.CharField(max_length=20, blank=True)

    match_status = models.CharField(
        max_length=20, choices=MatchStatus.choices, default=MatchStatus.UNKNOWN
    )
    matched_intent = models.ForeignKey(
        PaymentIntent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="settlement_items",
    )
    detail = models.CharField(max_length=255, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("match_status", "external_reference")
        indexes = [models.Index(fields=["match_status"])]

    def __str__(self) -> str:
        return f"{self.external_reference} · {self.match_status}"
