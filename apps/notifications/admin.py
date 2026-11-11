from django.contrib import admin

from .models import Notification, NotificationTemplate


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "channel", "active", "updated_at")
    list_filter = ("channel", "active")
    search_fields = ("key", "subject")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "person",
        "channel",
        "template_key",
        "status",
        "attempts",
        "created_at",
    )
    list_filter = ("channel", "status")
    search_fields = ("person__full_name", "template_key", "provider_ref")
    autocomplete_fields = ("person",)
    readonly_fields = ("created_at",)