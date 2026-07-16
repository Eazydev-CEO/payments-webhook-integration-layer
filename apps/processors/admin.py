from django.contrib import admin

from .models import PaymentProcessor


@admin.register(PaymentProcessor)
class PaymentProcessorAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "supports_webhooks", "mode_label")
    list_filter = ("is_active", "supports_webhooks", "code")
    search_fields = ("name", "code")
