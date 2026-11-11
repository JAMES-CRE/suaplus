from django.contrib import admin

from .models import (
    CorrectionRequest,
    Credential,
    CredentialReview,
    Evidence,
    SelfAssessment,
)


@admin.register(Credential)
class CredentialAdmin(admin.ModelAdmin):
    list_display = (
        "person",
        "title",
        "kind",
        "source",
        "review_status",
        "status",
        "issued_at",
    )
    list_filter = ("kind", "source", "review_status", "status")
    search_fields = ("title", "issuer", "credential_id", "person__full_name")
    autocomplete_fields = ("person",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(CredentialReview)
class CredentialReviewAdmin(admin.ModelAdmin):
    list_display = ("credential", "from_status", "to_status", "actor", "at")
    list_filter = ("to_status",)
    search_fields = ("credential__title", "credential__person__full_name")


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = (
        "person",
        "competency",
        "level",
        "type",
        "status",
        "assessed_at",
    )
    list_filter = ("type", "status", "competency__framework")
    search_fields = ("person__full_name", "competency__code")
    autocomplete_fields = ("person", "competency", "level")
    readonly_fields = ("assessed_at",)


@admin.register(SelfAssessment)
class SelfAssessmentAdmin(admin.ModelAdmin):
    list_display = ("person", "template", "status", "started_at", "submitted_at")
    list_filter = ("status", "template")
    search_fields = ("person__full_name",)
    autocomplete_fields = ("person", "template")


@admin.register(CorrectionRequest)
class CorrectionRequestAdmin(admin.ModelAdmin):
    list_display = ("person", "session", "resolved_at", "created_at")
    list_filter = ("resolved_at",)
    search_fields = ("person__full_name", "message")
    autocomplete_fields = ("person", "session")