"""Library views - Competency Library governance and UI."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.permissions import is_super_admin
from apps.library.models import (
    Competency,
    CompetencyDraft,
    CertificationCatalogue,
    Crosswalk,
    CrosswalkDraft,
    Domain,
    DomainDraft,
    EvidenceRule,
    EvidenceRuleDraft,
    Framework,
    FrameworkDraft,
    Level,
    LevelDraft,
    SurveyTemplate,
)
from apps.library.versioning import record_library_publication
from apps.passport.models import Credential, Evidence, SelfAssessment
from apps.programmes.models import ProgrammeCompetency
from apps.sessions.models import SessionCompetency, SurveyResponse
from apps.library.transfer import IMPORTABLE_SHEETS, SCHEMAS, export_csv, export_xlsx, parse_upload


def _can_edit_library(user):
    """Check if user is a super admin or library curator."""
    if is_super_admin(user):
        return True
    try:
        roles = user.hub_account.roles.values_list("role", flat=True)
        return "library_curator" in roles
    except AttributeError:
        return False


@login_required
def library_dashboard(request):
    """Main Library view showing frameworks, domains, and competencies."""
    from django.db.models import Count, Q

    if not _can_edit_library(request.user):
        messages.error(request, "You do not have permission to edit the library.")
        return redirect("hub_home")

    # All frameworks - clickable list
    frameworks = Framework.objects.all().order_by("-is_knust", "code")
    all_domains = Domain.objects.select_related("framework").order_by(
        "framework__code", "display_order", "code"
    )

    search_term = request.GET.get("q", "").strip()[:100]
    search_framework_id = request.GET.get("library_framework", "")
    search_domain_id = request.GET.get("library_domain", "")
    status_filter = request.GET.get("status", "")
    valid_statuses = {value for value, _label in Competency.STATUS_CHOICES}
    if not search_framework_id.isdigit():
        search_framework_id = ""
    if not search_domain_id.isdigit():
        search_domain_id = ""
    if status_filter not in valid_statuses:
        status_filter = ""
    search_active = bool(
        search_term or search_framework_id or search_domain_id or status_filter
    )
    search_results = Competency.objects.select_related(
        "framework", "domain"
    ).annotate(usage_count=Count("programme_targets"))
    if search_term:
        search_results = search_results.filter(
            Q(code__icontains=search_term)
            | Q(title__icontains=search_term)
            | Q(description__icontains=search_term)
            | Q(proficient_descriptor__icontains=search_term)
            | Q(domain__name__icontains=search_term)
            | Q(framework__name__icontains=search_term)
        )
    if search_framework_id:
        search_results = search_results.filter(framework_id=search_framework_id)
    if search_domain_id:
        search_results = search_results.filter(domain_id=search_domain_id)
    if status_filter in valid_statuses:
        search_results = search_results.filter(status=status_filter)
    search_results = search_results.order_by(
        "framework__code", "domain__display_order", "code"
    ) if search_active else Competency.objects.none()

    # Selected framework 
    framework_id = request.GET.get("framework")
    if framework_id:
        framework = get_object_or_404(Framework, id=framework_id)
    else:
        framework = frameworks.filter(is_knust=True).first() or frameworks.first()

    domains = []
    domain = None
    competencies = []
    levels = []

    if framework:
        domains = framework.domains.all().order_by("display_order")

        # Selected domain (default first)
        domain_id = request.GET.get("domain")
        if domain_id:
            domain = get_object_or_404(Domain, id=domain_id, framework=framework)
        elif domains:
            domain = domains.first()

        if domain:
            competencies = (
                domain.competencies.all()
                .annotate(usage_count=Count("programme_targets"))
                .order_by("code")
            )

        # Framework-level descriptors (5 levels)
        levels = framework.levels.all().order_by("ordinal")

    return render(
        request,
        "library/dashboard.html",
        {
            "frameworks": frameworks,
            "framework": framework,
            "domains": domains,
            "domain": domain,
            "competencies": competencies,
            "levels": levels,
            "all_domains": all_domains,
            "search_results": search_results,
            "search_active": search_active,
            "search_term": search_term,
            "search_framework_id": search_framework_id,
            "search_domain_id": search_domain_id,
            "status_filter": status_filter,
            "competency_statuses": Competency.STATUS_CHOICES,
        },
    )


def _transfer_text(row, field, default=""):
    value = row.get(field)
    return default if value is None else str(value).strip()


def _transfer_bool(value):
    value = str(value or "").strip().lower()
    if value in {"true", "1", "yes", "y"}:
        return True
    if value in {"false", "0", "no", "n", ""}:
        return False
    raise ValueError(f"'{value}' is not a valid true/false value.")


def _transfer_int(value, field):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{field}' must be a whole number.")


def _create_import_drafts(tables, user, reason):
    """Translate supported import rows into unpublished drafts atomically."""
    created = 0
    seen_codes = set()

    def find_framework(code):
        if not code:
            raise ValueError("framework_code is required.")
        framework = Framework.objects.filter(code=code).first()
        if framework is None:
            framework = FrameworkDraft.objects.filter(
                code=code, status="draft"
            ).first()
        return framework

    with transaction.atomic():
        for sheet in ["frameworks", "domains", "levels", "competencies", "crosswalks", "evidence_rules"]:
            for row_number, row in enumerate(tables.get(sheet, []), start=2):
                try:
                    if sheet == "frameworks":
                        code = _transfer_text(row, "code")
                        if not code:
                            raise ValueError("code is required.")
                        key = (sheet, code)
                        framework = Framework.objects.filter(code=code).first()
                        if framework is None and key in seen_codes:
                            raise ValueError("duplicate framework code in this import.")
                        existing_draft = FrameworkDraft.objects.filter(
                            code=code, status="draft"
                        ).first()
                        FrameworkDraft.objects.create(
                            framework=framework,
                            code=code,
                            name=_transfer_text(row, "name"),
                            publisher=_transfer_text(row, "publisher"),
                            version=_transfer_text(row, "version"),
                            source=_transfer_text(row, "source", "manual"),
                            is_knust=_transfer_bool(row.get("is_knust")),
                            proposed_status=_transfer_text(row, "status", "active"),
                            change_reason=reason,
                            created_by=user,
                        )
                        seen_codes.add(key)
                        created += 1

                    elif sheet == "domains":
                        framework_code = _transfer_text(row, "framework_code")
                        framework = Framework.objects.filter(code=framework_code).first()
                        if framework is None:
                            raise ValueError(f"framework '{framework_code}' must exist before importing domains.")
                        code = _transfer_text(row, "code")
                        if not code:
                            raise ValueError("code is required.")
                        key = (sheet, framework_code, code)
                        if key in seen_codes:
                            raise ValueError("duplicate domain code in this import.")
                        domain = Domain.objects.filter(framework=framework, code=code).first()
                        DomainDraft.objects.create(
                            domain=domain,
                            framework=framework,
                            code=code,
                            name=_transfer_text(row, "name"),
                            description=_transfer_text(row, "description"),
                            colour=_transfer_text(row, "colour", "#002147"),
                            display_order=_transfer_int(row.get("display_order", 0), "display_order"),
                            proposed_status=_transfer_text(row, "status", "active"),
                            change_reason=reason,
                            created_by=user,
                        )
                        seen_codes.add(key)
                        created += 1

                    elif sheet == "levels":
                        framework_code = _transfer_text(row, "framework_code")
                        framework = find_framework(framework_code)
                        if framework is None or isinstance(framework, FrameworkDraft):
                            raise ValueError(f"framework '{framework_code}' must exist before importing levels.")
                        ordinal = _transfer_int(row.get("ordinal"), "ordinal")
                        if ordinal < 1 or ordinal > 5:
                            raise ValueError("ordinal must be between 1 and 5.")
                        level = Level.objects.filter(framework=framework, ordinal=ordinal).first()
                        LevelDraft.objects.create(
                            level=level,
                            framework=framework,
                            ordinal=ordinal,
                            label=_transfer_text(row, "label"),
                            descriptor=_transfer_text(row, "descriptor"),
                            change_reason=reason,
                            created_by=user,
                        )
                        created += 1

                    elif sheet == "competencies":
                        framework_code = _transfer_text(row, "framework_code")
                        framework = Framework.objects.filter(code=framework_code).first()
                        domain = Domain.objects.filter(
                            framework=framework, code=_transfer_text(row, "domain_code")
                        ).first() if framework else None
                        if domain is None:
                            raise ValueError("framework_code/domain_code do not identify an existing domain.")
                        code = _transfer_text(row, "code")
                        key = (sheet, framework_code, code)
                        if not code or key in seen_codes:
                            raise ValueError("code is required and must not repeat in this import.")
                        competency = Competency.objects.filter(framework=framework, code=code).first()
                        replacement_code = _transfer_text(row, "replaced_by_code")
                        replacement = Competency.objects.filter(framework=framework, code=replacement_code, status__in=["active", "published"]).first() if replacement_code else None
                        proposed_status = _transfer_text(row, "status", "active")
                        if proposed_status == "retired" and replacement is None:
                            raise ValueError("retired competency rows need an existing active replaced_by_code.")
                        CompetencyDraft.objects.create(
                            competency=competency,
                            domain=domain,
                            code=code,
                            title=_transfer_text(row, "title"),
                            description=_transfer_text(row, "description"),
                            proficient_descriptor=_transfer_text(row, "proficient_descriptor"),
                            proposed_status=proposed_status,
                            proposed_replaced_by=replacement,
                            change_reason=reason,
                            created_by=user,
                        )
                        seen_codes.add(key)
                        created += 1

                    elif sheet == "crosswalks":
                        source_framework = Framework.objects.filter(code=_transfer_text(row, "knust_framework_code")).first()
                        target_framework = Framework.objects.filter(code=_transfer_text(row, "external_framework_code")).first()
                        source = Competency.objects.filter(framework=source_framework, code=_transfer_text(row, "knust_competency_code")).first() if source_framework else None
                        target = Competency.objects.filter(framework=target_framework, code=_transfer_text(row, "external_competency_code")).first() if target_framework else None
                        if source is None or target is None:
                            raise ValueError("crosswalk framework/competency codes must identify existing competencies.")
                        ordinal_value = row.get("external_level_ordinal")
                        external_level = None
                        if ordinal_value not in (None, ""):
                            external_level = Level.objects.filter(
                                framework=target_framework,
                                ordinal=_transfer_int(ordinal_value, "external_level_ordinal"),
                            ).first()
                            if external_level is None:
                                raise ValueError("external_level_ordinal does not exist in the external framework.")
                        crosswalk = Crosswalk.objects.filter(
                            knust_competency=source,
                            external_competency=target,
                            external_level=external_level,
                        ).first()
                        CrosswalkDraft.objects.create(
                            crosswalk=crosswalk,
                            knust_competency=source,
                            external_competency=target,
                            external_level=external_level,
                            note=_transfer_text(row, "note"),
                            change_reason=reason,
                            created_by=user,
                        )
                        created += 1

                    else:
                        framework_code = _transfer_text(row, "framework_code")
                        competency_code = _transfer_text(row, "competency_code")
                        framework = Framework.objects.filter(code=framework_code).first() if framework_code else None
                        competency = Competency.objects.filter(framework=framework, code=competency_code).first() if framework and competency_code else None
                        if competency_code and competency is None:
                            raise ValueError("competency_code does not exist in the selected framework.")
                        rule = EvidenceRule.objects.filter(
                            evidence_type=_transfer_text(row, "evidence_type"),
                            competency=competency,
                        ).first()
                        EvidenceRuleDraft.objects.create(
                            evidence_rule=rule,
                            evidence_type=_transfer_text(row, "evidence_type"),
                            competency=competency,
                            max_level=_transfer_int(row.get("max_level"), "max_level"),
                            description=_transfer_text(row, "description"),
                            change_reason=reason,
                            created_by=user,
                        )
                        created += 1
                except Exception as exc:
                    raise ValueError(f"{sheet} row {row_number}: {exc}") from exc

    return created


@login_required
def library_transfer(request):
    """Download Library data or import CSV/XLSX rows into unpublished drafts."""
    if not _can_edit_library(request.user):
        messages.error(request, "You do not have permission to manage the Library.")
        return redirect("hub_home")

    entity_names = sorted(SCHEMAS)
    importable_entities = sorted(IMPORTABLE_SHEETS)
    if request.method == "GET" and request.GET.get("export"):
        export_format = request.GET.get("export")
        entity = request.GET.get("entity", "competencies")
        if export_format == "csv":
            try:
                payload = export_csv(entity)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("library_transfer")
            response = HttpResponse(payload, content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="suaplus-library-{entity}.csv"'
            return response
        if export_format == "xlsx":
            response = HttpResponse(
                export_xlsx(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = 'attachment; filename="suaplus-library.xlsx"'
            return response
        messages.error(request, "Choose CSV or XLSX export.")
        return redirect("library_transfer")

    error = ""
    imported_count = None
    if request.method == "POST":
        upload = request.FILES.get("file")
        reason = request.POST.get("change_reason", "").strip()
        if not upload:
            error = "Choose a CSV or XLSX file."
        elif not reason:
            error = "Provide a reason for the imported Library changes."
        else:
            try:
                tables = parse_upload(upload, request.POST.get("entity", ""))
                imported_count = _create_import_drafts(tables, request.user, reason)
                messages.success(
                    request,
                    f"Imported {imported_count} row(s) as drafts. Review and publish them from the Library admin.",
                )
                return redirect("library_transfer")
            except ValueError as exc:
                error = str(exc)

    return render(
        request,
        "library/transfer.html",
        {
            "entities": entity_names,
            "importable_entities": importable_entities,
            "error": error,
            "imported_count": imported_count,
        },
    )


@login_required
def library_where_used(request, item_type, item_id):
    """Show dependent Library and training records without participant details."""
    if not _can_edit_library(request.user):
        messages.error(request, "You do not have permission to view the Library.")
        return redirect("hub_home")

    model_map = {
        "framework": Framework,
        "domain": Domain,
        "competency": Competency,
        "crosswalk": Crosswalk,
        "level": Level,
        "evidence-rule": EvidenceRule,
        "certification": CertificationCatalogue,
        "survey": SurveyTemplate,
    }
    model = model_map.get(item_type)
    if model is None:
        from django.http import Http404

        raise Http404("Unknown Library item type.")
    item = get_object_or_404(model, id=item_id)
    sections = []

    def add_section(title, items, count=None):
        items = list(items)
        sections.append({"title": title, "items": items[:100], "count": count if count is not None else len(items)})

    if item_type == "framework":
        competencies = Competency.objects.filter(framework=item)
        targets = ProgrammeCompetency.objects.filter(
            competency__framework=item
        ).select_related("programme", "competency")
        coverages = SessionCompetency.objects.filter(
            competency__framework=item
        ).select_related("session", "competency")
        add_section("Domains", (f"{d.code} · {d.name} · {d.get_status_display()}" for d in item.domains.all()), item.domains.count())
        add_section("Proficiency levels", (f"{level.ordinal} · {level.label}" for level in item.levels.all()), item.levels.count())
        add_section("Competencies", (f"{c.code} · {c.title} · {c.get_status_display()}" for c in competencies), competencies.count())
        add_section("Programme targets", (f"{pc.programme.name} → {pc.competency.code} (level {pc.target_level})" for pc in targets), targets.count())
        add_section("Sessions", (f"{sc.session.title} → {sc.competency.code}" for sc in coverages), coverages.count())
        add_section("Evidence records", [], Evidence.objects.filter(competency__framework=item).count())
        crosswalks = Crosswalk.objects.filter(knust_competency__framework=item)
        add_section("Crosswalks", (str(cw) for cw in crosswalks), crosswalks.count())
    elif item_type == "domain":
        competencies = item.competencies.all()
        targets = ProgrammeCompetency.objects.filter(
            competency__domain=item
        ).select_related("programme", "competency")
        coverages = SessionCompetency.objects.filter(
            competency__domain=item
        ).select_related("session", "competency")
        add_section("Competencies", (f"{c.code} · {c.title} · {c.get_status_display()}" for c in competencies), competencies.count())
        add_section("Programme targets", (f"{pc.programme.name} → {pc.competency.code} (level {pc.target_level})" for pc in targets), targets.count())
        add_section("Sessions", (f"{sc.session.title} → {sc.competency.code}" for sc in coverages), coverages.count())
        add_section("Evidence records", [], Evidence.objects.filter(competency__domain=item).count())
    elif item_type == "competency":
        targets = ProgrammeCompetency.objects.filter(competency=item).select_related("programme")
        coverages = SessionCompetency.objects.filter(competency=item).select_related("session")
        evidence_count = Evidence.objects.filter(competency=item).count()
        add_section("Programme targets", (f"{pc.programme.name} (level {pc.target_level})" for pc in targets), targets.count())
        add_section("Sessions", (sc.session.title for sc in coverages), coverages.count())
        add_section("Evidence records", [], evidence_count)
        from apps.library.models import CertificationCompetency

        certifications = CertificationCompetency.objects.filter(
            competency=item
        ).select_related("certification")
        add_section("Sanctioned certifications", (f"{row.certification.issuer} · {row.certification.title} (max level {row.max_level})" for row in certifications), certifications.count())
        add_section("Mapped from", (str(row) for row in item.crosswalks_from.select_related("external_competency")), item.crosswalks_from.count())
        add_section("Mapped to", (str(row) for row in item.crosswalks_to.select_related("knust_competency")), item.crosswalks_to.count())
    elif item_type == "crosswalk":
        source = item.knust_competency
        targets = ProgrammeCompetency.objects.filter(competency=source).select_related("programme")
        add_section("Source competency programme targets", (f"{pc.programme.name} (level {pc.target_level})" for pc in targets), targets.count())
        add_section("Source competency evidence records", [], Evidence.objects.filter(competency=source).count())
        add_section("Source competency sessions", (row.session.title for row in SessionCompetency.objects.filter(competency=source).select_related("session")), SessionCompetency.objects.filter(competency=source).count())
    elif item_type == "level":
        evidence = Evidence.objects.filter(level=item)
        add_section("Evidence records", [], evidence.count())
        crosswalks = Crosswalk.objects.filter(external_level=item)
        add_section("Crosswalks", (str(row) for row in crosswalks.select_related("knust_competency", "external_competency")), crosswalks.count())
    elif item_type == "evidence-rule":
        if item.competency_id:
            competencies = [item.competency]
        else:
            competencies = list(Competency.objects.filter(framework__is_knust=True))
        add_section("Affected competencies", (f"{c.code} · {c.title}" for c in competencies), len(competencies))
        if item.competency_id:
            evidence_count = Evidence.objects.filter(
                competency=item.competency, type=item.evidence_type
            ).count()
        else:
            evidence_count = Evidence.objects.filter(
                type=item.evidence_type, competency__framework__is_knust=True
            ).count()
        add_section("Evidence records governed by this rule", [], evidence_count)
    elif item_type == "certification":
        mappings = item.competencies.select_related("competency")
        add_section(
            "Mapped competencies",
            (f"{row.competency.code} · {row.competency.title} (max level {row.max_level})" for row in mappings),
            mappings.count(),
        )
        add_section(
            "Participant credentials",
            [],
            Credential.objects.filter(catalogue=item).count(),
        )
        add_section(
            "Generated evidence records",
            [],
            Evidence.objects.filter(credential__catalogue=item).count(),
        )
    else:
        add_section("Session survey responses", [], SurveyResponse.objects.filter(template=item).count())
        add_section("Baseline self-assessments", [], SelfAssessment.objects.filter(template=item).count())

    return render(
        request,
        "library/where_used.html",
        {
            "item_type": item_type,
            "item": item,
            "sections": sections,
            "reports_affected": [
                "Competency profiles and progression",
                "Programme competency progress",
                "Coverage and evidence summaries",
            ],
        },
    )




@login_required
def competency_edit(request, competency_id):
    """Draft an edit for a competency (Draft → Publish)."""
    if not _can_edit_library(request.user):
        messages.error(request, "You do not have permission.")
        return redirect("hub_home")

    competency = get_object_or_404(Competency, id=competency_id)

    if request.method == "POST":
        change_reason = request.POST.get("change_reason", "").strip()
        proposed_status = request.POST.get("proposed_status")
        if proposed_status not in {"active", "retired"}:
            proposed_status = competency.status
        replacement = None
        if not change_reason:
            messages.error(request, "Provide a reason before saving this draft.")
            return redirect("competency_edit", competency_id=competency.id)
        if proposed_status == "retired":
            replacement_id = request.POST.get("proposed_replaced_by")
            replacement = Competency.objects.filter(
                id=replacement_id,
                framework=competency.framework,
                status__in=["active", "published"],
            ).exclude(id=competency.id).first()
            if replacement is None:
                messages.error(
                    request,
                    "Choose an active replacement competency in the same framework before retiring this competency.",
                )
                return redirect("competency_edit", competency_id=competency.id)
        draft = CompetencyDraft.objects.create(
            competency=competency,
            domain=competency.domain,
            code=competency.code,
            title=request.POST.get("title", competency.title).strip(),
            description=request.POST.get("description", competency.description).strip(),
            proficient_descriptor=request.POST.get(
                "proficient_descriptor", competency.proficient_descriptor
            ).strip(),
            proposed_status=proposed_status,
            proposed_replaced_by=replacement,
            change_reason=change_reason,
            created_by=request.user,
        )
        messages.success(request, "Draft saved. Review the impact before publishing.")
        return redirect("competency_draft_detail", draft_id=draft.id)

    programmes_using = ProgrammeCompetency.objects.filter(
        competency=competency
    ).select_related("programme")
    sessions_using = SessionCompetency.objects.filter(
        competency=competency
    ).select_related("session")
    evidence_count = Evidence.objects.filter(competency=competency).count()

    return render(
        request,
        "library/competency_edit.html",
        {
            "competency": competency,
            "programmes_using": programmes_using,
            "sessions_using": sessions_using,
            "evidence_count": evidence_count,
            "replacements": Competency.objects.filter(
                framework=competency.framework,
                status__in=["active", "published"],
            ).exclude(id=competency.id).order_by("code"),
        },
    )


@login_required
def competency_draft_detail(request, draft_id):
    """Show impact before publication and require a second curator for descriptors."""
    if not _can_edit_library(request.user):
        messages.error(request, "You do not have permission.")
        return redirect("hub_home")

    draft = get_object_or_404(
        CompetencyDraft.objects.select_related(
            "competency", "created_by", "proposed_replaced_by"
        ),
        id=draft_id,
    )
    competency = draft.competency
    domain = draft.domain or (competency.domain if competency else None)
    programmes_using = (
        ProgrammeCompetency.objects.filter(competency=competency).select_related("programme")
        if competency
        else ProgrammeCompetency.objects.none()
    )
    sessions_using = (
        SessionCompetency.objects.filter(competency=competency).select_related("session")
        if competency
        else SessionCompetency.objects.none()
    )
    evidence_count = Evidence.objects.filter(competency=competency).count() if competency else 0
    requires_second_curator = draft.proficient_descriptor != (
        competency.proficient_descriptor if competency else ""
    )

    if request.method == "POST" and draft.status != "published":
        if requires_second_curator and draft.status == "draft":
            draft.status = "awaiting_approval"
            draft.save(update_fields=["status"])
            messages.info(
                request,
                "This proficiency descriptor change needs approval from another Library Curator.",
            )
        elif requires_second_curator and draft.created_by_id == request.user.id:
            messages.error(request, "A second curator must publish this draft.")
        elif draft.proposed_status == "retired" and (
            draft.proposed_replaced_by_id is None
            or draft.proposed_replaced_by.framework_id != (domain.framework_id if domain else None)
            or draft.proposed_replaced_by.status not in {"active", "published"}
        ):
            messages.error(
                request,
                "This retirement needs an active replacement competency in the same framework.",
            )
        else:
            with transaction.atomic():
                before = {
                    "code": competency.code,
                    "title": competency.title,
                    "description": competency.description,
                    "proficient_descriptor": competency.proficient_descriptor,
                    "status": competency.status,
                    "replaced_by_id": competency.replaced_by_id,
                } if competency else {}
                if competency is None:
                    competency = Competency(
                        framework=domain.framework,
                        domain=domain,
                    )
                elif draft.domain_id:
                    competency.domain = draft.domain
                    competency.framework = draft.domain.framework
                competency.code = draft.code
                competency.title = draft.title
                competency.description = draft.description
                competency.proficient_descriptor = draft.proficient_descriptor
                competency.status = draft.proposed_status
                competency.replaced_by = (
                    draft.proposed_replaced_by
                    if draft.proposed_status == "retired"
                    else None
                )
                competency.save()

                draft.status = "published"
                draft.competency = competency
                draft.approved_by = request.user if requires_second_curator else None
                draft.published_at = timezone.now()
                draft.save(
                    update_fields=[
                        "competency",
                        "status",
                        "approved_by",
                        "published_at",
                    ]
                )

                record_library_publication(
                    "competency",
                    competency,
                    draft.change_reason,
                    request.user,
                    approved_by=draft.approved_by,
                )

                from apps.audit.services import log_event

                log_event(
                    request=request,
                    action="library_publish",
                    entity="Competency",
                    entity_id=competency.id,
                    before=before,
                    after={
                        "title": competency.title,
                        "code": competency.code,
                        "description": competency.description,
                        "proficient_descriptor": competency.proficient_descriptor,
                        "status": competency.status,
                        "domain_id": competency.domain_id,
                        "replaced_by_id": competency.replaced_by_id,
                        "draft_id": draft.id,
                        "reason": draft.change_reason,
                    },
                )
            messages.success(request, f"Published changes to {competency.code}.")

    return render(
        request,
        "library/competency_draft_detail.html",
        {
            "draft": draft,
            "competency": competency,
            "domain": domain,
            "programmes_using": programmes_using,
            "sessions_using": sessions_using,
            "evidence_count": evidence_count,
            "requires_second_curator": requires_second_curator,
        },
    )
    
@login_required
def competency_new(request):
    """Create a competency draft for a selected Library domain."""
    if not _can_edit_library(request.user):
        messages.error(request, "You do not have permission.")
        return redirect("hub_home")

    domains = Domain.objects.filter(status__in=["active", "published"]).select_related(
        "framework"
    ).order_by("framework__code", "display_order", "code")
    domain_id = request.POST.get("domain") or request.GET.get("domain")
    selected_domain = get_object_or_404(domains, id=domain_id) if domain_id else None

    if request.method == "POST":
        code = request.POST.get("code", "").strip()
        title = request.POST.get("title", "").strip()
        reason = request.POST.get("change_reason", "").strip()
        selected_domain = get_object_or_404(domains, id=request.POST.get("domain"))
        error = None
        if not code or not title or not reason:
            error = "Code, title, and reason for change are required."
        elif Competency.objects.filter(
            framework=selected_domain.framework, code=code
        ).exists():
            error = "That competency code is already in use in this framework."
        elif CompetencyDraft.objects.filter(
            domain__framework=selected_domain.framework,
            code=code,
            status__in=["draft", "awaiting_approval"],
        ).exists():
            error = "A draft with that competency code is already awaiting publication."
        if error:
            return render(
                request,
                "library/competency_new.html",
                {"domains": domains, "selected_domain": selected_domain, "error": error},
            )

        draft = CompetencyDraft.objects.create(
            domain=selected_domain,
            code=code,
            title=title,
            description=request.POST.get("description", "").strip(),
            proficient_descriptor=request.POST.get("proficient_descriptor", "").strip(),
            proposed_status="active",
            change_reason=reason,
            created_by=request.user,
        )
        messages.success(request, "Competency draft saved. Review its impact before publishing.")
        return redirect("competency_draft_detail", draft_id=draft.id)

    return render(
        request,
        "library/competency_new.html",
        {"domains": domains, "selected_domain": selected_domain},
    )
