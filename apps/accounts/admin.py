from django.contrib import admin

# Register your models here.
from django.contrib import admin

from .models import RoleAssignment, UserAccount


class RoleAssignmentInline(admin.TabularInline):
    model = RoleAssignment
    extra = 0
    fk_name = "user_account"
    fields = ("role", "scope_type", "scope_id", "granted_by")


@admin.register(UserAccount)
class UserAccountAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "auth_method",
        "mfa_enabled",
        "idle_timeout_minutes",
        "created_at",
    )
    list_filter = ("auth_method", "mfa_enabled")
    search_fields = ("user__email", "person__full_name")
    autocomplete_fields = ("person",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [RoleAssignmentInline]


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "user_account",
        "role",
        "scope_type",
        "scope_id",
        "granted_at",
    )
    list_filter = ("role", "scope_type")
    search_fields = ("user_account__user__email",)
    autocomplete_fields = ("user_account",)
    
    
