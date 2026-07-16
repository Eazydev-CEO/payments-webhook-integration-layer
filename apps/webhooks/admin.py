from django.contrib import admin

from .models import WebhookDeliveryAttempt, WebhookEvent


class WebhookDeliveryAttemptInline(admin.TabularInline):
    model = WebhookDeliveryAttempt
    extra = 0
    readonly_fields = ("attempt_number", "result", "detail", "created_at")
    can_delete = False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_id", "processor", "event_type", "signature_verified",
        "is_duplicate", "status", "retry_count", "received_at",
    )
    list_filter = ("processor", "status", "signature_verified", "is_duplicate")
    search_fields = ("event_id", "event_type")
    readonly_fields = ("received_at", "processed_at", "normalized", "headers", "raw_payload")
    inlines = [WebhookDeliveryAttemptInline]


@admin.register(WebhookDeliveryAttempt)
class WebhookDeliveryAttemptAdmin(admin.ModelAdmin):
    list_display = ("event", "attempt_number", "result", "created_at")
    list_filter = ("result",)
