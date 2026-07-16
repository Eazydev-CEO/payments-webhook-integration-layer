from django.contrib import admin

from .models import CRMDelivery


@admin.register(CRMDelivery)
class CRMDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "id", "target", "event_type", "status", "retry_count",
        "next_retry_at", "created_at",
    )
    list_filter = ("target", "status", "event_type")
    search_fields = ("source_event_id", "event_type")
    readonly_fields = ("created_at", "updated_at")
