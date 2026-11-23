"""Reporting views."""
from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.permissions import (
    aggregate_only,
    can,
    is_executive,
    is_super_admin,
    scope_records,
    visible_memos,
)
from apps.core.models import AcademicPeriod
from apps.programmes.models import Partner, Programme, TierStatus
from apps.registry.models import OrgUnit, Person
from apps.reporting.access import scoped_reporting
from apps.reporting.exports import dashboard_rows, export_report, suppress_small_counts
from apps.reporting.services.kpis import (
    kpi_ai_coverage_by_college,
    kpi_attendances_by_month,
    kpi_baseline_coverage,
    kpi_cascade,
    kpi_cascade_by_trainer,
    kpi_coverage,
    kpi_coverage_heatmap,
    kpi_csat,
    kpi_csat_trend,
    kpi_external_reach,
    kpi_pipeline,
    kpi_progression,
    kpi_progression_by_domain,
    kpi_reach,
    kpi_reach_by_unit,
    kpi_workshops_held,
    suppress,
)
from apps.reporting.services.memo import generate_memo
from apps.sessions.models import Attendance, Session


def _resolve_dashboard_period(request):
    """Resolve an SRS period selection into inclusive aware datetime bounds."""
    academic_periods = list(AcademicPeriod.objects.all())
    calendar_years = sorted(
        {value.year for value in Session.objects.dates("start_at", "year", order="DESC")},
        reverse=True,
    )
    quarters = [
        {"value": f"quarter-{year}-Q{quarter}", "label": f"Q{quarter} {year}"}
        for year in calendar_years
        for quarter in range(1, 5)
    ]

    selected = request.GET.get("period", "all")
    since = until = None
    error = ""
    def aware_bounds(start_date, end_date):
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
        end = timezone.make_aware(datetime.combine(end_date, time.max), tz)
        return start, end

    if selected == "custom":
        date_from = request.GET.get("date_from", "").strip()
        date_to = request.GET.get("date_to", "").strip()
        try:
            start_date = date.fromisoformat(date_from)
            end_date = date.fromisoformat(date_to)
            if start_date > end_date:
                raise ValueError
            since, until = aware_bounds(start_date, end_date)
        except ValueError:
            error = "Enter a valid start and end date, with the start date on or before the end date."
    elif selected.startswith("academic-"):
        period_id = selected.removeprefix("academic-")
        period = next((item for item in academic_periods if str(item.id) == period_id), None)
        if period:
            since, until = aware_bounds(period.start_date, period.end_date)
        else:
            selected = "all"
            error = "Choose a valid academic year or semester."
    elif selected.startswith("calendar-"):
        year_part = selected.removeprefix("calendar-")
        if year_part.isdigit() and int(year_part) in calendar_years:
            year = int(year_part)
            since, until = aware_bounds(date(year, 1, 1), date(year, 12, 31))
        else:
            selected = "all"
            error = "Choose a calendar year that has report data."
    elif selected.startswith("quarter-"):
        parts = selected.split("-")
        try:
            year = int(parts[1])
            quarter = int(parts[2][1:])
            if year not in calendar_years or quarter not in range(1, 5):
                raise ValueError
            month = (quarter - 1) * 3 + 1
            start_date = date(year, month, 1)
            if quarter == 4:
                end_date = date(year, 12, 31)
            else:
                end_date = date(year, month + 3, 1) - timedelta(days=1)
            since, until = aware_bounds(start_date, end_date)
        except (IndexError, ValueError):
            selected = "all"
            error = "Choose a valid calendar quarter that has report data."
    elif selected == "30d":
        since = timezone.now() - timedelta(days=30)
    elif selected == "90d":
        since = timezone.now() - timedelta(days=90)
    elif selected == "year":
        since = timezone.now() - timedelta(days=365)
    elif selected != "all":
        selected = "all"
        error = "Choose a valid reporting period."

    return {
        "selected_period": selected,
        "since": since,
        "until": until,
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
        "period_error": error,
        "academic_periods": academic_periods,
        "calendar_years": calendar_years,
        "quarters": quarters,
    }


def _selected_record(request, queryset, key):
    identifier = request.GET.get(key, "").strip()
    if not identifier:
        return None
    if not identifier.isdecimal():
        raise PermissionDenied
    record = queryset.filter(pk=identifier).first()
    if record is None:
        raise PermissionDenied
    return record


@login_required
@scoped_reporting("view_dashboard")
def dashboard_overview(request):
    """Scope every base KPI query before applying optional filters."""
    all_data = is_super_admin(request.user) or is_executive(request.user)
    programme_qs = scope_records(Programme.objects.all(), request.user, "view_dashboard")
    unit_qs = scope_records(OrgUnit.objects.all(), request.user, "view_dashboard")
    programme = _selected_record(request, programme_qs, "programme")
    unit = _selected_record(request, unit_qs, "unit")

    programme_id = str(programme.id) if programme else ""
    unit_id = str(unit.id) if unit else ""
    category = request.GET.get("category", "").strip()
    valid_categories = list(
        scope_records(Person.objects.all(), request.user, "view_dashboard").exclude(category="").order_by("category")
        .values_list("category", flat=True).distinct()
    )
    if category not in valid_categories:
        category = ""
    affiliation = request.GET.get("affiliation", "").strip()
    valid_affiliations = {value for value, _label in Person.AFFILIATION_CHOICES}
    if affiliation not in valid_affiliations:
        affiliation = ""
    partner_id = request.GET.get("partner", "").strip()
    partner_qs = scope_records(Partner.objects.select_related("organisation"), request.user, "view_dashboard")
    partners = partner_qs.distinct().order_by("organisation__name", "role")
    partner = partners.filter(pk=partner_id).first() if partner_id else None
    if partner_id and partner is None:
        raise PermissionDenied
    partner_id = str(partner.id) if partner else ""
    period_data = _resolve_dashboard_period(request)
    period = period_data["selected_period"]
    since = period_data["since"]
    until = period_data["until"]

    # Compute existing KPIs
    scope_filters = {
        "category": category or None,
        "affiliation": affiliation or None,
        "partner_id": partner.id if partner else None,
    }
    reach = kpi_reach(programme=programme, unit=unit, since=since, until=until, **scope_filters)
    coverage = kpi_coverage(programme=programme, unit=unit, since=since, until=until, **scope_filters)
    progression = kpi_progression(programme=programme, unit=unit, since=since, until=until, **scope_filters)
    csat = kpi_csat(programme=programme, unit=unit, since=since, until=until, **scope_filters)
    cascade = kpi_cascade(programme=programme, since=since, until=until, **scope_filters)
    pipeline = kpi_pipeline(programme=programme, since=since, until=until, **scope_filters)
    reach_by_unit = kpi_reach_by_unit(programme=programme, since=since, until=until, **scope_filters)

    # Compute new KPIs
    workshops_held = kpi_workshops_held(programme=programme, unit=unit, since=since, until=until, **scope_filters)
    baseline = kpi_baseline_coverage(programme=programme, unit=unit, since=since, until=until, **scope_filters)
    ai_coverage = [] if not all_data else kpi_ai_coverage_by_college(since=since, until=until)
    monthly_reach = kpi_attendances_by_month(programme=programme, unit=unit, since=since, until=until, **scope_filters)
    csat_trend = kpi_csat_trend(programme=programme, unit=unit, since=since, until=until, **scope_filters)
    progression_by_domain = kpi_progression_by_domain(programme=programme, unit=unit, since=since, until=until, **scope_filters)
    coverage_heatmap = [] if not all_data else kpi_coverage_heatmap(
        programme=programme, unit=unit, since=since, until=until, **scope_filters
    )
    external_reach = kpi_external_reach(
        programme=programme, unit=unit, since=since, until=until, **scope_filters
    )
    cascade_by_trainer = [] if is_executive(request.user) and not is_super_admin(request.user) else kpi_cascade_by_trainer(
        programme=programme, since=since, until=until, **scope_filters
    )

    # Active trainers = Certified + Master
    active_trainers = (pipeline.get("Certified", 0) or 0) + (pipeline.get("Master", 0) or 0)

    counts_suppressed = is_executive(request.user) and not is_super_admin(request.user)
    dashboard_data = {
        "reach": reach,
        "workshops_held": workshops_held,
        "active_trainers": active_trainers,
        "cascade": cascade,
        "csat": csat,
        "baseline": baseline,
        "coverage": coverage,
        "progression": progression,
        "pipeline": pipeline,
        "reach_by_unit": reach_by_unit,
        "ai_coverage": ai_coverage,
        "monthly_reach": monthly_reach,
        "csat_trend": csat_trend,
        "progression_by_domain": progression_by_domain,
        "coverage_heatmap": coverage_heatmap,
        "external_reach": external_reach,
        "cascade_by_trainer": cascade_by_trainer,
    }
    dashboard_data = suppress_small_counts(
        dashboard_data, threshold=5 if counts_suppressed else 0
    )
    reach = dashboard_data["reach"]
    workshops_held = dashboard_data["workshops_held"]
    active_trainers = dashboard_data["active_trainers"]
    cascade = dashboard_data["cascade"]
    csat = dashboard_data["csat"]
    baseline = dashboard_data["baseline"]
    coverage = dashboard_data["coverage"]
    progression = dashboard_data["progression"]
    pipeline = dashboard_data["pipeline"]
    reach_by_unit = dashboard_data["reach_by_unit"]
    ai_coverage = dashboard_data["ai_coverage"]
    monthly_reach = dashboard_data["monthly_reach"]
    csat_trend = dashboard_data["csat_trend"]
    progression_by_domain = dashboard_data["progression_by_domain"]
    coverage_heatmap = dashboard_data["coverage_heatmap"]
    external_reach = dashboard_data["external_reach"]
    cascade_by_trainer = dashboard_data["cascade_by_trainer"]

    # Filter choices for the form
    programmes = programme_qs.filter(status="active").order_by("name")
    units = unit_qs.filter(type__in=["college", "directorate"]).order_by("name")

    export_format = request.GET.get("export", "").lower()
    if export_format and not can(request.user, "export_reports"):
        raise PermissionDenied
    if export_format:
        rows = dashboard_rows(dashboard_data)
        rows.extend(
            ("Scope filters", label, value)
            for label, value in (
                ("Programme", programme.name if programme else "All programmes"),
                ("Unit", unit.name if unit else "All units"),
                ("Category", category or "All categories"),
                ("Affiliation", affiliation or "Internal and external"),
                ("Partner", partner.organisation.name if partner else "All partners"),
            )
        )
        filters = {
            "period": period,
            "date_from": period_data["date_from"],
            "date_to": period_data["date_to"],
            "programme_id": programme_id,
            "unit_id": unit_id,
            "category": category,
            "affiliation": affiliation,
            "partner_id": partner_id,
        }
        try:
            return export_report(
                rows,
                export_format=export_format,
                actor=request.user,
                view_name="dashboard",
                filters=filters,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("dashboard")

    return render(
        request,
        "reporting/dashboard.html",
        {
            "reach": reach,
            "workshops_held": workshops_held,
            "active_trainers": active_trainers,
            "cascade": cascade,
            "csat": csat,
            "baseline": baseline,
            "coverage": coverage,
            "progression": progression,
            "pipeline": pipeline,
            "reach_by_unit": reach_by_unit,
            "ai_coverage": ai_coverage,
            "monthly_reach": monthly_reach,
            "csat_trend": csat_trend,
            "progression_by_domain": progression_by_domain,
            "coverage_heatmap": coverage_heatmap,
            "external_reach": external_reach,
            "cascade_by_trainer": cascade_by_trainer,
            "programmes": programmes,
            "units": units,
            "categories": valid_categories,
            "affiliations": Person.AFFILIATION_CHOICES,
            "partners": partners,
            "selected_category": category,
            "selected_affiliation": affiliation,
            "selected_partner": partner_id,
            "selected_programme": programme_id or "",
            "selected_unit": unit_id or "",
            "selected_period": period,
            "selected_date_from": period_data["date_from"],
            "selected_date_to": period_data["date_to"],
            "period_error": period_data["period_error"],
            "academic_periods": period_data["academic_periods"],
            "calendar_years": period_data["calendar_years"],
            "quarters": period_data["quarters"],
            "counts_suppressed": counts_suppressed,
            "can_export": can(request.user, "export_reports"),
        },
    )


@login_required
@scoped_reporting("view_dashboard")
def drilldown(request, kpi):
    """Personal drill-down is scoped; executive-only access stays aggregated."""
    programme_qs = scope_records(Programme.objects.all(), request.user, "view_dashboard")
    unit_qs = scope_records(OrgUnit.objects.all(), request.user, "view_dashboard")
    selected_programme = _selected_record(request, programme_qs, "programme")
    selected_unit = _selected_record(request, unit_qs, "unit")
    programme_id = selected_programme.pk if selected_programme else None
    unit_id = selected_unit.pk if selected_unit else None
    data_capability = "view_dashboard" if aggregate_only(request.user) else "view_people"
    
    from django.http import Http404
    if kpi not in {"reach", "pipeline"}:
        raise Http404("Unknown drill-down")
    rows = []
    title = kpi.title()

    if kpi == "reach":
        title = "Reach"
        qs = scope_records(Attendance.objects.all(), request.user, data_capability).filter(
            status__in=["completed", "partial"]
        ).select_related("person", "session__programme")

        if programme_id:
            qs = qs.filter(session__programme_id=programme_id)
        if unit_id:
            qs = qs.filter(person__unit_id=unit_id)

        if aggregate_only(request.user):
            from django.db.models import Count
            rows = [{"name": row["session__programme__name"], "detail": f"{suppress(row['people'])} people", "when": None}
                    for row in qs.values("session__programme__name").annotate(people=Count("person_id", distinct=True))]
        else:
            seen = set()
            for a in qs.order_by("-check_in_at"):
                if a.person_id not in seen:
                    seen.add(a.person_id)
                    rows.append({
                        "name": a.person.full_name,
                        "detail": a.session.programme.name if a.session.programme else "—",
                        "when": a.check_in_at,
                    })

    elif kpi == "pipeline":
        title = "Trainer Pipeline"
        qs = scope_records(TierStatus.objects.all(), request.user, data_capability).filter(
            status="approved"
        ).select_related("person", "programme")

        if programme_id:
            qs = qs.filter(programme_id=programme_id)
        if unit_id:
            qs = qs.filter(person__unit_id=unit_id)

        if aggregate_only(request.user):
            from django.db.models import Count
            rows = [{"name": row["programme__name"], "detail": f"Tier {row['tier']}: {suppress(row['people'])} people", "when": None}
                    for row in qs.values("programme__name", "tier").annotate(people=Count("person_id", distinct=True))]
        else:
            for ts in qs.order_by("-approved_at"):
                rows.append({
                    "name": ts.person.full_name,
                    "detail": f"{ts.get_tier_display()} in {ts.programme.name}",
                    "when": ts.approved_at,
                })

    counts_suppressed = aggregate_only(request.user)
    small_group_suppressed = counts_suppressed and 0 < qs.values("person_id").distinct().count() < 5
    if small_group_suppressed:
        rows = []

    export_format = request.GET.get("export", "").lower()
    if export_format and not can(request.user, "export_reports"):
        raise PermissionDenied
    if export_format:
        export_rows = [
            (
                title,
                row["name"],
                " · ".join(
                    str(value) for value in (row.get("detail"), row.get("when")) if value
                ),
            )
            for row in rows
        ]
        try:
            return export_report(
                export_rows,
                export_format=export_format,
                actor=request.user,
                view_name=f"drilldown-{kpi}",
                filters={
                    "programme_id": programme_id or "",
                    "unit_id": unit_id or "",
                },
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("drilldown", kpi=kpi)

    return render(
        request,
        "reporting/drilldown.html",
        {
            "title": title,
            "rows": rows,
            "kpi": kpi,
            "selected_programme": selected_programme,
            "selected_unit": selected_unit,
            "small_group_suppressed": small_group_suppressed,
        },
    )


@login_required
@scoped_reporting("export_reports")
def memo_list(request):
    memos = visible_memos(request.user).select_related("created_by").order_by("-created_at")

    return render(
        request,
        "reporting/memo_list.html",
        {"memos": memos, "count": memos.count()},
    )


@login_required
@scoped_reporting("export_reports")
def memo_generate(request):
    programmes = scope_records(Programme.objects.filter(status="active"), request.user, "export_reports").order_by("name")

    if request.method == "POST":
        period_label = request.POST.get("period_label", "").strip()
        programme_id = request.POST.get("programme", "").strip()
        since_str = request.POST.get("since", "").strip()

        if not period_label:
            messages.error(request, "Please enter a period label.")
            return render(
                request,
                "reporting/memo_generate.html",
                {"programmes": programmes, "form_data": request.POST},
            )

        programme = None
        if programme_id:
            if not programme_id.isdecimal():
                raise PermissionDenied
            programme = get_object_or_404(programmes, id=programme_id)

        since = None
        if since_str:
            try:
                since = datetime.strptime(since_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                messages.error(request, "Invalid 'since' date. Use YYYY-MM-DD.")
                return render(
                    request,
                    "reporting/memo_generate.html",
                    {"programmes": programmes, "form_data": request.POST},
                )

        memo = generate_memo(
            programme=programme,
            period_label=period_label,
            since=since,
            created_by=request.user,
        )

        messages.success(
            request,
            f"Memo '{memo.period_label}' generated successfully.",
        )
        return redirect("memo_detail", memo_id=memo.id)

    return render(
        request,
        "reporting/memo_generate.html",
        {"programmes": programmes},
    )


@login_required
@scoped_reporting("export_reports")
def memo_detail(request, memo_id):
    memo = get_object_or_404(visible_memos(request.user), id=memo_id)
    counts_suppressed = is_executive(request.user) and not is_super_admin(request.user)
    display_figures = (
        suppress_small_counts(memo.figures) if counts_suppressed else memo.figures
    )

    display_narrative = memo.scope.get("executive_narrative", "This legacy memo requires a privacy review.") if counts_suppressed else memo.narrative
    export_format = request.GET.get("export", "").lower()
    if export_format and not can(request.user, "export_reports"):
        raise PermissionDenied
    if export_format:
        rows = dashboard_rows(display_figures)
        rows.extend(
            ("Narrative", f"Line {index}", line)
            for index, line in enumerate(display_narrative.splitlines(), start=1)
        )
        try:
            return export_report(
                rows,
                export_format=export_format,
                actor=request.user,
                view_name="executive-memo",
                filters={"memo_id": memo.id, "period_label": memo.period_label},
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("memo_detail", memo_id=memo.id)

    previous_versions = (
        visible_memos(request.user).filter(
            period_label=memo.period_label,
            scope=memo.scope,
        )
        .exclude(id=memo.id)
        .order_by("-version")
    )

    return render(
        request,
        "reporting/memo_detail.html",
        {
            "memo": memo,
            "display_narrative": display_narrative,
            "display_figures": display_figures,
            "counts_suppressed": counts_suppressed,
            "can_export": can(request.user, "export_reports"),
            "previous_versions": previous_versions,
        },
    )
