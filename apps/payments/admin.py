from django.contrib import admin

from .models import PaymentIntent, PaymentTransaction


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = (
        "reference", "customer_name", "amount", "currency",
        "processor", "status", "created_at",
    )
    list_filter = ("status", "processor", "currency", "created_at")
    search_fields = ("reference", "idempotency_key", "customer_name", "customer_email")
    readonly_fields = ("reference", "created_at", "updated_at")


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "reference", "intent", "amount", "currency", "status", "created_at",
    )
    list_filter = ("status", "processor", "created_at")
    search_fields = ("reference", "external_id")
