from django.contrib import admin

from .models import (
    Nomination,
    Partner,
    Programme,
    ProgrammeCompetency,
    ProgrammeOwner,
    ProgrammePartner,
    TierCriterion,
    TierStatus,
)


class ProgrammeCompetencyInline(admin.TabularInline):
    model = ProgrammeCompetency
    extra = 0
    autocomplete_fields = ("competency",)


class ProgrammePartnerInline(admin.TabularInline):
    model = ProgrammePartner
    extra = 0


class TierCriterionInline(admin.TabularInline):
    model = TierCriterion
    extra = 0


@admin.register(Programme)
class ProgrammeAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "is_template", "created_at")
    list_filter = ("status", "is_template")
    search_fields = ("name", "description")
    inlines = [
        ProgrammeCompetencyInline,
        ProgrammePartnerInline,
        TierCriterionInline,
    ]


@admin.register(ProgrammeOwner)
class ProgrammeOwnerAdmin(admin.ModelAdmin):
    list_display = ("programme", "user")
    list_filter = ("programme",)


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ("organisation", "role")
    list_filter = ("role",)
    search_fields = ("organisation__name",)


@admin.register(ProgrammeCompetency)
class ProgrammeCompetencyAdmin(admin.ModelAdmin):
    list_display = ("programme", "competency", "target_level")
    list_filter = ("programme", "competency__framework")
    search_fields = ("competency__code", "programme__name")


@admin.register(TierCriterion)
class TierCriterionAdmin(admin.ModelAdmin):
    list_display = ("programme", "tier", "rule_type")
    list_filter = ("programme", "tier", "rule_type")


@admin.register(TierStatus)
class TierStatusAdmin(admin.ModelAdmin):
    list_display = (
        "person",
        "programme",
        "tier",
        "status",
        "criteria_met",
        "criteria_total",
    )
    list_filter = ("programme", "tier", "status")
    search_fields = ("person__full_name",)


@admin.register(Nomination)
class NominationAdmin(admin.ModelAdmin):
    list_display = ("person", "programme", "tier", "decision", "created_at")
    list_filter = ("programme", "tier", "decision")
    search_fields = ("person__full_name",)