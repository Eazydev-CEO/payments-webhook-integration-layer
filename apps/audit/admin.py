from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "summary", "actor", "entity_type", "entity_id")
    list_filter = ("action", "created_at")
    search_fields = ("summary", "entity_id", "entity_type")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False
