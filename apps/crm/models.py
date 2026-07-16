from __future__ import annotations

from django.db import models

from apps.common.retry import RetryableJob
from apps.payments.models import PaymentIntent


class CRMTarget(models.TextChoices):
    HUBSPOT = "hubspot", "HubSpot (demo)"
    ZOHO = "zoho", "Zoho (demo)"
    INTERNAL = "internal", "Internal CRM (demo)"


class CRMDelivery(RetryableJob):
    """A simulated forward of a normalized payment event to an external CRM."""

    target = models.CharField(max_length=20, choices=CRMTarget.choices)
    payment_intent = models.ForeignKey(
        PaymentIntent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="crm_deliveries",
    )
    # Link back to the originating webhook event (string id to avoid a hard dep).
    source_event_id = models.CharField(max_length=120, blank=True)
    event_type = models.CharField(max_length=60, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "CRM delivery"
        verbose_name_plural = "CRM deliveries"
        indexes = [
            models.Index(fields=["status", "next_retry_at"]),
            models.Index(fields=["target", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_target_display()} · {self.event_type} ({self.status})"
