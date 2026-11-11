from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("at", "actor", "action", "entity", "entity_id")
    list_filter = ("action", "entity")
    search_fields = ("entity_id", "actor__email")
    readonly_fields = (
        "actor",
        "action",
        "entity",
        "entity_id",
        "before",
        "after",
        "ip",
        "at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False