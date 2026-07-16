from __future__ import annotations

from django.db import models


class ProcessorCode(models.TextChoices):
    STRIPE = "stripe", "Stripe"
    PAYSTACK = "paystack", "Paystack"
    MANUAL = "manual", "Manual / Internal Demo"


class PaymentProcessor(models.Model):
    """A configured payment processor integration."""

    code = models.CharField(
        max_length=20, choices=ProcessorCode.choices, unique=True
    )
    name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)
    supports_webhooks = models.BooleanField(default=True)
    # Non-secret display config only (secrets live in env vars).
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def is_live(self) -> bool:
        """Whether real credentials are configured for this processor."""
        from django.conf import settings

        if self.code == ProcessorCode.STRIPE:
            return bool(settings.STRIPE_SECRET_KEY)
        if self.code == ProcessorCode.PAYSTACK:
            return bool(settings.PAYSTACK_SECRET_KEY)
        return False

    @property
    def mode_label(self) -> str:
        if self.code == ProcessorCode.MANUAL:
            return "Demo"
        return "Live" if self.is_live else "Demo"
