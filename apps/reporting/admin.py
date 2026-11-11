from django.contrib import admin

from .models import ExportLog, Memo, Target


@admin.register(Target)
class TargetAdmin(admin.ModelAdmin):
    list_display = ("kpi", "programme", "unit", "category", "period", "value")
    list_filter = ("kpi", "period", "programme", "unit")
    search_fields = ("kpi",)


@admin.register(Memo)
class MemoAdmin(admin.ModelAdmin):
    list_display = ("period_label", "version", "used_ai", "created_by", "created_at")
    list_filter = ("used_ai",)
    search_fields = ("period_label",)
    readonly_fields = ("created_at",)


@admin.register(ExportLog)
class ExportLogAdmin(admin.ModelAdmin):
    list_display = ("actor", "view_name", "format", "row_count", "at")
    list_filter = ("format",)
    search_fields = ("view_name", "actor__email")
    readonly_fields = ("actor", "view_name", "filters", "format", "row_count", "at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False