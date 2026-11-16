from django.contrib import admin
from django.contrib import messages
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone

from apps.audit.services import log_event
from apps.library.views import _can_edit_library
from apps.library.versioning import record_library_publication
from .models import (
    CertificationCatalogue,
    CertificationCompetency,
    CrosswalkDraft,
    DomainDraft,
    EvidenceRuleDraft,
    FrameworkDraft,
    LibraryVersion,
    LevelDraft,
)


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
    inlines = []

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)


@admin.register(FrameworkDraft)
class FrameworkDraftAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "proposed_status",
        "status",
        "created_by",
        "created_at",
        "impact_summary",
    )
    list_filter = ("status", "proposed_status", "is_knust")
    search_fields = ("code", "name", "publisher", "change_reason")
    readonly_fields = ("status", "created_by", "published_at", "impact_summary")
    fields = (
        "framework",
        "code",
        "name",
        "publisher",
        "version",
        "source",
        "is_knust",
        "proposed_status",
        "change_reason",
        "status",
        "created_by",
        "published_at",
        "impact_summary",
    )
    actions = ("publish_drafts",)

    def has_module_permission(self, request):
        return _can_edit_library(request.user)

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)

    def has_add_permission(self, request):
        return _can_edit_library(request.user)

    def has_change_permission(self, request, obj=None):
        if not _can_edit_library(request.user):
            return False
        return obj is None or obj.status == "draft"

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Impact preview")
    def impact_summary(self, obj):
        if not obj.framework_id:
            return "New framework; no existing competency records affected"
        framework = obj.framework
        competencies = framework.competencies.all()
        programme_count = sum(item.programme_targets.count() for item in competencies)
        session_count = sum(item.session_coverages.count() for item in competencies)
        evidence_count = sum(item.evidence.count() for item in competencies)
        return (
            f"{framework.domains.count()} domains; {competencies.count()} competencies; "
            f"{programme_count} programme targets; {session_count} sessions; "
            f"{evidence_count} evidence records"
        )

    @admin.action(description="Publish selected framework drafts")
    def publish_drafts(self, request, queryset):
        published_count = 0
        for draft_id in queryset.values_list("id", flat=True):
            with transaction.atomic():
                draft = FrameworkDraft.objects.select_for_update().get(id=draft_id)
                if draft.status != "draft":
                    continue

                framework = draft.framework
                before = {}
                if framework:
                    before = {
                        "code": framework.code,
                        "name": framework.name,
                        "publisher": framework.publisher,
                        "version": framework.version,
                        "source": framework.source,
                        "is_knust": framework.is_knust,
                        "status": framework.status,
                    }
                else:
                    framework = Framework()

                framework.code = draft.code
                framework.name = draft.name
                framework.publisher = draft.publisher
                framework.version = draft.version
                framework.source = draft.source
                framework.is_knust = draft.is_knust
                framework.status = draft.proposed_status
                if draft.proposed_status in ("published", "active"):
                    framework.published_at = timezone.now()
                framework.save()

                draft.framework = framework
                draft.status = "published"
                draft.published_at = timezone.now()
                draft.save(update_fields=["framework", "status", "published_at"])
                record_library_publication(
                    "framework", framework, draft.change_reason, request.user
                )
                log_event(
                    request=request,
                    action="library_framework_publish",
                    entity="Framework",
                    entity_id=framework.id,
                    before=before,
                    after={
                        "code": framework.code,
                        "name": framework.name,
                        "publisher": framework.publisher,
                        "version": framework.version,
                        "source": framework.source,
                        "is_knust": framework.is_knust,
                        "status": framework.status,
                        "reason": draft.change_reason,
                    },
                )
                published_count += 1

        if published_count:
            self.message_user(request, f"Published {published_count} framework draft(s).", messages.SUCCESS)


@admin.register(LibraryVersion)
class LibraryVersionAdmin(admin.ModelAdmin):
    list_display = (
        "item_type",
        "item_id",
        "version_number",
        "framework",
        "framework_revision",
        "created_by",
        "approved_by",
        "created_at",
    )
    list_filter = ("item_type", "framework", "created_at")
    readonly_fields = (
        "framework",
        "item_type",
        "item_id",
        "version_number",
        "framework_revision",
        "snapshot",
        "change_reason",
        "created_by",
        "approved_by",
        "created_at",
    )
    fields = readonly_fields

    def has_module_permission(self, request):
        return _can_edit_library(request.user)

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "framework", "display_order", "status")
    list_filter = ("framework", "status")
    search_fields = ("code", "name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)


@admin.register(DomainDraft)
class DomainDraftAdmin(admin.ModelAdmin):
    list_display = (
        "framework",
        "code",
        "name",
        "proposed_status",
        "status",
        "created_by",
        "created_at",
        "impact_summary",
    )
    list_filter = ("status", "framework", "proposed_status")
    search_fields = ("code", "name", "change_reason")
    readonly_fields = ("status", "created_by", "published_at", "impact_summary")
    fields = (
        "domain",
        "framework",
        "code",
        "name",
        "description",
        "colour",
        "display_order",
        "proposed_status",
        "change_reason",
        "status",
        "created_by",
        "published_at",
        "impact_summary",
    )
    actions = ("publish_drafts",)

    def has_module_permission(self, request):
        return _can_edit_library(request.user)

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)

    def has_add_permission(self, request):
        return _can_edit_library(request.user)

    def has_change_permission(self, request, obj=None):
        if not _can_edit_library(request.user):
            return False
        return obj is None or obj.status == "draft"

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Impact preview")
    def impact_summary(self, obj):
        if not obj.domain_id:
            return "New domain; no existing competency records affected"
        competencies = obj.domain.competencies.all()
        programme_count = sum(item.programme_targets.count() for item in competencies)
        session_count = sum(item.session_coverages.count() for item in competencies)
        evidence_count = sum(item.evidence.count() for item in competencies)
        return (
            f"{competencies.count()} competencies; {programme_count} programme targets; "
            f"{session_count} sessions; {evidence_count} evidence records"
        )

    @admin.action(description="Publish selected domain drafts")
    def publish_drafts(self, request, queryset):
        published_count = 0
        for draft_id in queryset.values_list("id", flat=True):
            with transaction.atomic():
                draft = DomainDraft.objects.select_for_update().get(id=draft_id)
                if draft.status != "draft":
                    continue

                domain = draft.domain
                before = {}
                if domain:
                    before = {
                        "framework": domain.framework_id,
                        "code": domain.code,
                        "name": domain.name,
                        "description": domain.description,
                        "colour": domain.colour,
                        "display_order": domain.display_order,
                        "status": domain.status,
                    }
                else:
                    domain = Domain(framework=draft.framework)

                domain.framework = draft.framework
                domain.code = draft.code
                domain.name = draft.name
                domain.description = draft.description
                domain.colour = draft.colour
                domain.display_order = draft.display_order
                domain.status = draft.proposed_status
                domain.save()

                draft.domain = domain
                draft.status = "published"
                draft.published_at = timezone.now()
                draft.save(update_fields=["domain", "status", "published_at"])
                record_library_publication(
                    "domain", domain, draft.change_reason, request.user
                )
                log_event(
                    request=request,
                    action="library_domain_publish",
                    entity="Domain",
                    entity_id=domain.id,
                    before=before,
                    after={
                        "framework": domain.framework_id,
                        "code": domain.code,
                        "name": domain.name,
                        "description": domain.description,
                        "colour": domain.colour,
                        "display_order": domain.display_order,
                        "status": domain.status,
                        "reason": draft.change_reason,
                    },
                )
                published_count += 1

        if published_count:
            self.message_user(request, f"Published {published_count} domain draft(s).", messages.SUCCESS)


@admin.register(Competency)
class CompetencyAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "domain", "framework", "status")
    list_filter = ("framework", "domain", "status")
    search_fields = ("code", "title", "description")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)


@admin.register(Level)
class LevelAdmin(admin.ModelAdmin):
    list_display = ("framework", "ordinal", "label", "where_used_link")
    list_filter = ("framework",)
    ordering = ("framework", "ordinal")
    search_fields = ("label", "framework__code")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)

    @admin.display(description="Where used")
    def where_used_link(self, obj):
        return format_html(
            '<a href="{}">View dependencies</a>',
            reverse("library_where_used", args=["level", obj.id]),
        )


@admin.register(LevelDraft)
class LevelDraftAdmin(admin.ModelAdmin):
    list_display = (
        "framework",
        "ordinal",
        "label",
        "status",
        "created_by",
        "created_at",
        "impact_summary",
    )
    list_filter = ("status", "framework", "ordinal")
    search_fields = ("label", "descriptor", "change_reason")
    readonly_fields = ("status", "created_by", "approved_by", "published_at", "impact_summary")
    fields = (
        "level",
        "framework",
        "ordinal",
        "label",
        "descriptor",
        "change_reason",
        "status",
        "created_by",
        "approved_by",
        "published_at",
        "impact_summary",
    )
    actions = ("request_second_approval", "approve_and_publish")

    def has_module_permission(self, request):
        return _can_edit_library(request.user)

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)

    def has_add_permission(self, request):
        return _can_edit_library(request.user)

    def has_change_permission(self, request, obj=None):
        if not _can_edit_library(request.user):
            return False
        return obj is None or obj.status == "draft"

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Impact preview")
    def impact_summary(self, obj):
        from apps.passport.models import Evidence

        evidence_count = (
            Evidence.objects.filter(level=obj.level).count()
            if obj.level_id
            else Evidence.objects.filter(
                competency__framework=obj.framework, level__ordinal=obj.ordinal
            ).count()
        )
        return (
            f"{obj.framework.competencies.count()} competencies in framework; "
            f"{evidence_count} evidence records at this level"
        )

    @admin.action(description="Request second-curator approval for selected drafts")
    def request_second_approval(self, request, queryset):
        count = queryset.filter(status="draft").update(status="awaiting_approval")
        self.message_user(request, f"{count} level draft(s) now await a second curator.", messages.SUCCESS)

    @admin.action(description="Approve and publish selected drafts")
    def approve_and_publish(self, request, queryset):
        published_count = 0
        blocked_count = 0
        for draft_id in queryset.values_list("id", flat=True):
            with transaction.atomic():
                draft = LevelDraft.objects.select_for_update().get(id=draft_id)
                if draft.status != "awaiting_approval" or draft.created_by_id == request.user.id:
                    blocked_count += 1
                    continue

                level = draft.level
                before = {}
                if level:
                    before = {
                        "framework": level.framework_id,
                        "ordinal": level.ordinal,
                        "label": level.label,
                        "descriptor": level.descriptor,
                    }
                else:
                    level = Level(framework=draft.framework)

                level.framework = draft.framework
                level.ordinal = draft.ordinal
                level.label = draft.label
                level.descriptor = draft.descriptor
                level.save()

                draft.level = level
                draft.status = "published"
                draft.approved_by = request.user
                draft.published_at = timezone.now()
                draft.save(
                    update_fields=["level", "status", "approved_by", "published_at"]
                )
                record_library_publication(
                    "level",
                    level,
                    draft.change_reason,
                    request.user,
                    approved_by=request.user,
                )
                log_event(
                    request=request,
                    action="library_level_publish",
                    entity="Level",
                    entity_id=level.id,
                    before=before,
                    after={
                        "framework": level.framework_id,
                        "ordinal": level.ordinal,
                        "label": level.label,
                        "descriptor": level.descriptor,
                        "reason": draft.change_reason,
                    },
                )
                published_count += 1

        if published_count:
            self.message_user(request, f"Published {published_count} level draft(s).", messages.SUCCESS)
        if blocked_count:
            self.message_user(
                request,
                f"{blocked_count} draft(s) were skipped; approval must come from another curator.",
                messages.WARNING,
            )


@admin.register(Crosswalk)
class CrosswalkAdmin(admin.ModelAdmin):
    list_display = ("knust_competency", "external_competency", "external_level", "where_used_link")
    list_filter = ("knust_competency__framework", "external_competency__framework")
    search_fields = ("knust_competency__code", "external_competency__code")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)

    @admin.display(description="Where used")
    def where_used_link(self, obj):
        return format_html(
            '<a href="{}">View dependencies</a>',
            reverse("library_where_used", args=["crosswalk", obj.id]),
        )


@admin.register(CrosswalkDraft)
class CrosswalkDraftAdmin(admin.ModelAdmin):
    list_display = (
        "knust_competency",
        "external_competency",
        "status",
        "created_by",
        "created_at",
        "impact_summary",
    )
    list_filter = ("status", "knust_competency__framework")
    search_fields = (
        "knust_competency__code",
        "external_competency__code",
        "change_reason",
    )
    readonly_fields = ("status", "created_by", "approved_by", "published_at", "impact_summary")
    fields = (
        "crosswalk",
        "knust_competency",
        "external_competency",
        "external_level",
        "note",
        "change_reason",
        "status",
        "created_by",
        "approved_by",
        "published_at",
        "impact_summary",
    )
    actions = ("request_second_approval", "approve_and_publish")

    def has_module_permission(self, request):
        return _can_edit_library(request.user)

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)

    def has_add_permission(self, request):
        return _can_edit_library(request.user)

    def has_change_permission(self, request, obj=None):
        if not _can_edit_library(request.user):
            return False
        return obj is None or obj.status == "draft"

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Impact preview")
    def impact_summary(self, obj):
        competency = obj.knust_competency
        return (
            f"{competency.programme_targets.count()} programmes; "
            f"{competency.session_coverages.count()} sessions; "
            f"{competency.evidence.count()} evidence records"
        )

    @admin.action(description="Request second-curator approval for selected drafts")
    def request_second_approval(self, request, queryset):
        count = queryset.filter(status="draft").update(status="awaiting_approval")
        self.message_user(request, f"{count} draft(s) now await a second curator.", messages.SUCCESS)

    @admin.action(description="Approve and publish selected drafts")
    def approve_and_publish(self, request, queryset):
        published_count = 0
        blocked_count = 0
        for draft_id in queryset.values_list("id", flat=True):
            with transaction.atomic():
                draft = CrosswalkDraft.objects.select_for_update().get(id=draft_id)
                if draft.status != "awaiting_approval" or draft.created_by_id == request.user.id:
                    blocked_count += 1
                    continue

                crosswalk = draft.crosswalk
                before = {}
                if crosswalk:
                    before = {
                        "external_competency": crosswalk.external_competency_id,
                        "external_level": crosswalk.external_level_id,
                        "note": crosswalk.note,
                    }
                else:
                    crosswalk = Crosswalk(knust_competency=draft.knust_competency)

                crosswalk.external_competency = draft.external_competency
                crosswalk.external_level = draft.external_level
                crosswalk.note = draft.note
                crosswalk.save()

                draft.status = "published"
                draft.approved_by = request.user
                draft.published_at = timezone.now()
                draft.save(update_fields=["status", "approved_by", "published_at"])
                record_library_publication(
                    "crosswalk",
                    crosswalk,
                    draft.change_reason,
                    request.user,
                    approved_by=request.user,
                )
                log_event(
                    request=request,
                    action="library_crosswalk_publish",
                    entity="Crosswalk",
                    entity_id=crosswalk.id,
                    before=before,
                    after={
                        "knust_competency": draft.knust_competency_id,
                        "external_competency": draft.external_competency_id,
                        "external_level": draft.external_level_id,
                        "note": draft.note,
                        "reason": draft.change_reason,
                    },
                )
                published_count += 1

        if published_count:
            self.message_user(request, f"Published {published_count} crosswalk draft(s).", messages.SUCCESS)
        if blocked_count:
            self.message_user(
                request,
                f"{blocked_count} draft(s) were skipped; they must await approval by a different curator.",
                messages.WARNING,
            )


@admin.register(EvidenceRule)
class EvidenceRuleAdmin(admin.ModelAdmin):
    list_display = ("evidence_type", "max_level", "competency", "status", "where_used_link")
    list_filter = ("evidence_type", "status", "competency__framework")
    search_fields = ("competency__code", "description")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)

    @admin.display(description="Where used")
    def where_used_link(self, obj):
        return format_html(
            '<a href="{}">View dependencies</a>',
            reverse("library_where_used", args=["evidence-rule", obj.id]),
        )


@admin.register(EvidenceRuleDraft)
class EvidenceRuleDraftAdmin(admin.ModelAdmin):
    list_display = (
        "evidence_type",
        "competency",
        "max_level",
        "status",
        "created_by",
        "created_at",
        "impact_summary",
    )
    list_filter = ("status", "evidence_type", "competency__framework")
    search_fields = ("competency__code", "description", "change_reason")
    readonly_fields = ("status", "created_by", "approved_by", "published_at", "impact_summary")
    fields = (
        "evidence_rule",
        "evidence_type",
        "competency",
        "max_level",
        "description",
        "change_reason",
        "status",
        "created_by",
        "approved_by",
        "published_at",
        "impact_summary",
    )
    actions = ("request_second_approval", "approve_and_publish")

    def has_module_permission(self, request):
        return _can_edit_library(request.user)

    def has_view_permission(self, request, obj=None):
        return _can_edit_library(request.user)

    def has_add_permission(self, request):
        return _can_edit_library(request.user)

    def has_change_permission(self, request, obj=None):
        if not _can_edit_library(request.user):
            return False
        return obj is None or obj.status == "draft"

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Impact preview")
    def impact_summary(self, obj):
        from apps.passport.models import Evidence
        from apps.programmes.models import ProgrammeCompetency
        from apps.sessions.models import SessionCompetency

        if obj.competency_id:
            programmes = obj.competency.programme_targets.count()
            sessions = obj.competency.session_coverages.count()
            evidence = obj.competency.evidence.count()
        else:
            programmes = ProgrammeCompetency.objects.count()
            sessions = SessionCompetency.objects.count()
            evidence = Evidence.objects.count()
        return f"{programmes} programmes; {sessions} sessions; {evidence} evidence records"

    @admin.action(description="Request second-curator approval for selected drafts")
    def request_second_approval(self, request, queryset):
        count = queryset.filter(status="draft").update(status="awaiting_approval")
        self.message_user(request, f"{count} draft(s) now await a second curator.", messages.SUCCESS)

    @admin.action(description="Approve and publish selected drafts")
    def approve_and_publish(self, request, queryset):
        published_count = 0
        blocked_count = 0
        for draft_id in queryset.values_list("id", flat=True):
            with transaction.atomic():
                draft = EvidenceRuleDraft.objects.select_for_update().get(id=draft_id)
                if draft.status != "awaiting_approval" or draft.created_by_id == request.user.id:
                    blocked_count += 1
                    continue

                rule = draft.evidence_rule
                before = {}
                if rule:
                    before = {
                        "evidence_type": rule.evidence_type,
                        "competency": rule.competency_id,
                        "max_level": rule.max_level,
                        "description": rule.description,
                        "status": rule.status,
                    }
                else:
                    rule = EvidenceRule()

                rule.evidence_type = draft.evidence_type
                rule.competency = draft.competency
                rule.max_level = draft.max_level
                rule.description = draft.description
                rule.status = "active"
                rule.save()

                draft.status = "published"
                draft.approved_by = request.user
                draft.published_at = timezone.now()
                draft.save(update_fields=["status", "approved_by", "published_at"])
                record_library_publication(
                    "evidence_rule",
                    rule,
                    draft.change_reason,
                    request.user,
                    approved_by=request.user,
                )
                log_event(
                    request=request,
                    action="library_evidence_rule_publish",
                    entity="EvidenceRule",
                    entity_id=rule.id,
                    before=before,
                    after={
                        "evidence_type": rule.evidence_type,
                        "competency": rule.competency_id,
                        "max_level": rule.max_level,
                        "description": rule.description,
                        "reason": draft.change_reason,
                    },
                )
                published_count += 1

        if published_count:
            self.message_user(request, f"Published {published_count} evidence-rule draft(s).", messages.SUCCESS)
        if blocked_count:
            self.message_user(
                request,
                f"{blocked_count} draft(s) were skipped; they must await approval by a different curator.",
                messages.WARNING,
            )
    
@admin.register(SurveyTemplate)
class SurveyTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "version", "is_default", "status", "where_used_link")
    list_filter = ("type", "status", "is_default")
    search_fields = ("name",)   

    @admin.display(description="Where used")
    def where_used_link(self, obj):
        return format_html(
            '<a href="{}">View dependencies</a>',
            reverse("library_where_used", args=["survey", obj.id]),
        )
    

class CertificationCompetencyInline(admin.TabularInline):
    model = CertificationCompetency
    extra = 0
    autocomplete_fields = ("competency",)


@admin.register(CertificationCatalogue)
class CertificationCatalogueAdmin(admin.ModelAdmin):
    list_display = ("issuer", "title", "renewal_months", "active", "auto_approve", "where_used_link")
    list_filter = ("active", "auto_approve", "issuer")
    search_fields = ("issuer", "title")
    inlines = [CertificationCompetencyInline]

    @admin.display(description="Where used")
    def where_used_link(self, obj):
        return format_html(
            '<a href="{}">View dependencies</a>',
            reverse("library_where_used", args=["certification", obj.id]),
        )


@admin.register(CertificationCompetency)
class CertificationCompetencyAdmin(admin.ModelAdmin):
    list_display = ("certification", "competency", "max_level")
    list_filter = ("certification",)
    search_fields = ("competency__code", "certification__title")     
    
