from django.contrib import admin
from .models import CertificationCatalogue, CertificationCompetency


from .models import (
    Competency,
    Crosswalk,
    Domain,
    EvidenceRule,
    Framework,
    Level,
    SurveyTemplate,
)


class DomainInline(admin.TabularInline):
    model = Domain
    extra = 0
    fields = ("code", "name", "display_order", "status")


@admin.register(Framework)
class FrameworkAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_knust", "status", "version")
    list_filter = ("is_knust", "status")
    search_fields = ("code", "name", "publisher")
    inlines = [DomainInline]


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "framework", "display_order", "status")
    list_filter = ("framework", "status")
    search_fields = ("code", "name")


@admin.register(Competency)
class CompetencyAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "domain", "framework", "status")
    list_filter = ("framework", "domain", "status")
    search_fields = ("code", "title", "description")


@admin.register(Level)
class LevelAdmin(admin.ModelAdmin):
    list_display = ("framework", "ordinal", "label")
    list_filter = ("framework",)
    ordering = ("framework", "ordinal")
    search_fields = ("label", "framework__code")


@admin.register(Crosswalk)
class CrosswalkAdmin(admin.ModelAdmin):
    list_display = ("knust_competency", "external_competency", "external_level")
    list_filter = ("knust_competency__framework", "external_competency__framework")
    search_fields = ("knust_competency__code", "external_competency__code")


@admin.register(EvidenceRule)
class EvidenceRuleAdmin(admin.ModelAdmin):
    list_display = ("evidence_type", "max_level", "competency", "status")
    list_filter = ("evidence_type", "status", "competency__framework")
    search_fields = ("competency__code", "description")
    
@admin.register(SurveyTemplate)
class SurveyTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "version", "is_default", "status")
    list_filter = ("type", "status", "is_default")
    search_fields = ("name",)   
    

class CertificationCompetencyInline(admin.TabularInline):
    model = CertificationCompetency
    extra = 0
    autocomplete_fields = ("competency",)


@admin.register(CertificationCatalogue)
class CertificationCatalogueAdmin(admin.ModelAdmin):
    list_display = ("issuer", "title", "renewal_months", "active", "auto_approve")
    list_filter = ("active", "auto_approve", "issuer")
    search_fields = ("issuer", "title")
    inlines = [CertificationCompetencyInline]


@admin.register(CertificationCompetency)
class CertificationCompetencyAdmin(admin.ModelAdmin):
    list_display = ("certification", "competency", "max_level")
    list_filter = ("certification",)
    search_fields = ("competency__code", "certification__title")     
    