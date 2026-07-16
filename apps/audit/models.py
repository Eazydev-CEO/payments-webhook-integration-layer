from __future__ import annotations

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Immutable record of a security- or money-relevant action."""

    class Action(models.TextChoices):
        INTENT_CREATED = "intent_created", "Payment intent created"
        INTENT_DUPLICATE = "intent_duplicate", "Payment intent duplicate (idempotent)"
        PAYMENT_MARKED = "payment_marked", "Demo payment marked"
        WEBHOOK_RECEIVED = "webhook_received", "Webhook received"
        WEBHOOK_VERIFIED = "webhook_verified", "Webhook signature verified"
        WEBHOOK_REJECTED = "webhook_rejected", "Webhook signature rejected"
        WEBHOOK_DUPLICATE = "webhook_duplicate", "Webhook duplicate ignored"
        WEBHOOK_PROCESSED = "webhook_processed", "Webhook processed"
        WEBHOOK_RETRIED = "webhook_retried", "Webhook retried"
        CRM_DELIVERED = "crm_delivered", "CRM delivery succeeded"
        CRM_FAILED = "crm_failed", "CRM delivery failed"
        CRM_RETRIED = "crm_retried", "CRM delivery retried"
        SETTLEMENT_IMPORTED = "settlement_imported", "Settlement batch imported"
        RECONCILED = "reconciled", "Settlement reconciled"
        LOGIN = "login", "User logged in"
        LOGOUT = "logout", "User logged out"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=40, choices=Action.choices)
    entity_type = models.CharField(max_length=60, blank=True)
    entity_id = models.CharField(max_length=120, blank=True)
    summary = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["entity_type", "entity_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_action_display()} · {self.summary}"
