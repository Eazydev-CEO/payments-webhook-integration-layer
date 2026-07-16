from django.contrib import admin

from .models import SettlementBatch, SettlementItem


class SettlementItemInline(admin.TabularInline):
    model = SettlementItem
    extra = 0
    readonly_fields = (
        "external_reference", "amount", "currency", "reported_status",
        "match_status", "matched_intent", "detail",
    )
    can_delete = False


@admin.register(SettlementBatch)
class SettlementBatchAdmin(admin.ModelAdmin):
    list_display = (
        "reference", "processor", "statement_date", "expected_amount",
        "received_amount", "difference", "status",
    )
    list_filter = ("processor", "status", "statement_date")
    search_fields = ("reference",)
    inlines = [SettlementItemInline]


@admin.register(SettlementItem)
class SettlementItemAdmin(admin.ModelAdmin):
    list_display = (
        "external_reference", "batch", "amount", "currency", "match_status",
    )
    list_filter = ("match_status", "currency")
    search_fields = ("external_reference",)
