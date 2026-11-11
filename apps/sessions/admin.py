from django.contrib import admin

from .models import (
    Attendance,
    Facilitation,
    ImportBatch,
    ImportMappingTemplate,
    Session,
    SessionCompetency,
    SessionPartner,
    SurveyResponse,
)


class SessionCompetencyInline(admin.TabularInline):
    model = SessionCompetency
    extra = 0
    autocomplete_fields = ("competency",)


class SessionPartnerInline(admin.TabularInline):
    model = SessionPartner
    extra = 0


class FacilitationInline(admin.TabularInline):
    model = Facilitation
    extra = 0
    autocomplete_fields = ("person",)


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("title", "programme", "start_at", "mode", "status", "session_code")
    list_filter = ("programme", "mode", "status")
    search_fields = ("title", "session_code")
    readonly_fields = ("qr_secret", "session_code", "created_at", "updated_at")
    inlines = [SessionCompetencyInline, SessionPartnerInline, FacilitationInline]


@admin.register(Facilitation)
class FacilitationAdmin(admin.ModelAdmin):
    list_display = ("session", "person", "role")
    list_filter = ("role",)
    search_fields = ("person__full_name", "session__title")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("session", "person", "method", "status", "check_in_at", "check_out_at", "conflict")
    list_filter = ("method", "status", "conflict")
    search_fields = ("person__full_name", "session__title")
    autocomplete_fields = ("session", "person")


@admin.register(SurveyResponse)
class SurveyResponseAdmin(admin.ModelAdmin):
    list_display = ("attendance", "type", "csat", "submitted_at")
    list_filter = ("type", "csat")


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("session", "status", "created_by", "created_at", "undone_at")
    list_filter = ("status",)
    readonly_fields = ("created_at",)


@admin.register(ImportMappingTemplate)
class ImportMappingTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")
    search_fields = ("name",)