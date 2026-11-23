from typing import ClassVar

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import BaseInlineFormSet

from apps.passport.models import Evidence
from apps.sessions.services import grant_attendance_evidence

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


class SessionCompetencyFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if not self.instance.pk or not self.instance.attendances.exists():
            return
        for form in self.forms:
            if form.errors:
                continue
            if form.cleaned_data.get("DELETE") or (form.has_changed() and "competency" in form.changed_data):
                raise ValidationError("Competency mappings are locked after the first check-in.")


class SessionCompetencyInline(admin.TabularInline):
    formset = SessionCompetencyFormSet
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
    inlines: ClassVar[list] = [SessionCompetencyInline, SessionPartnerInline, FacilitationInline]


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

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            Session.objects.select_for_update().get(pk=obj.session_id)
            previous = Attendance.objects.filter(pk=obj.pk).values("status", "person_id", "session_id").first() if change else None
            super().save_model(request, obj, form, change)
            changed_source = previous is None or previous != {"status": obj.status, "person_id": obj.person_id, "session_id": obj.session_id}
            if obj.status == "completed" and changed_source:
                grant_attendance_evidence(obj)
            elif obj.status != "completed":
                Evidence.objects.filter(type="attendance", source_type="attendance", source_id=obj.pk).update(status="revoked")


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