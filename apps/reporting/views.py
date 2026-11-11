"""Reporting views."""
import json
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.permissions import get_user_scopes, is_super_admin
from apps.programmes.models import Programme, TierStatus
from apps.registry.models import OrgUnit
from apps.reporting.models import Memo
from apps.reporting.services.kpis import (
    kpi_ai_coverage_by_college,
    kpi_attendances_by_month,
    kpi_baseline_coverage,
    kpi_cascade,
    kpi_coverage,
    kpi_csat,
    kpi_pipeline,
    kpi_progression,
    kpi_reach,
    kpi_reach_by_unit,
    kpi_workshops_held,
)
from apps.reporting.services.memo import generate_memo
from apps.sessions.models import Attendance


@login_required
def dashboard_overview(request):
    """KPI tiles and charts, scoped to the user's permissions."""
    # Parse filters
    programme_id = request.GET.get("programme")
    unit_id = request.GET.get("unit")
    period = request.GET.get("period", "all")

    programme = Programme.objects.filter(id=programme_id).first() if programme_id else None
    unit = OrgUnit.objects.filter(id=unit_id).first() if unit_id else None

    since = None
    if period == "30d":
        since = timezone.now() - timedelta(days=30)
    elif period == "90d":
        since = timezone.now() - timedelta(days=90)
    elif period == "year":
        since = timezone.now() - timedelta(days=365)

    # Compute existing KPIs
    reach = kpi_reach(programme=programme, unit=unit, since=since)
    coverage = kpi_coverage(programme=programme, unit=unit, since=since)
    progression = kpi_progression(programme=programme, unit=unit, since=since)
    csat = kpi_csat(programme=programme, unit=unit, since=since)
    cascade = kpi_cascade(programme=programme)
    pipeline = kpi_pipeline(programme=programme)
    reach_by_unit = kpi_reach_by_unit(programme=programme, since=since)

    # Compute new KPIs
    workshops_held = kpi_workshops_held(programme=programme, unit=unit, since=since)
    baseline = kpi_baseline_coverage(programme=programme, unit=unit)
    ai_coverage = kpi_ai_coverage_by_college()
    monthly_reach = kpi_attendances_by_month(programme=programme, unit=unit, since=since)

    # Active trainers = Certified + Master
    active_trainers = (pipeline.get("Certified", 0) or 0) + (pipeline.get("Master", 0) or 0)

    # Filter choices for the form
    programmes = Programme.objects.filter(status="active").order_by("name")
    units = OrgUnit.objects.filter(type__in=["college", "directorate"]).order_by("name")

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
            "programmes": programmes,
            "units": units,
            "selected_programme": programme_id or "",
            "selected_unit": unit_id or "",
            "selected_period": period,
        },
    )


@login_required
def drilldown(request, kpi):
    """Drill-down list for a specific KPI."""
    if not is_super_admin(request.user):
        try:
            roles = set(request.user.hub_account.roles.values_list("role", flat=True))
        except AttributeError:
            roles = set()
        if "executive_viewer" not in roles:
            messages.error(request, "You don't have permission to view drill-downs.")
            return redirect("hub_home")

    programme_id = request.GET.get("programme")
    unit_id = request.GET.get("unit")
    
    rows = []
    title = kpi.title()

    if kpi == "reach":
        title = "Reach"
        qs = Attendance.objects.filter(
            status__in=["completed", "partial"]
        ).select_related("person", "session__programme")

        if programme_id:
            qs = qs.filter(session__programme_id=programme_id)
        if unit_id:
            qs = qs.filter(person__unit_id=unit_id)

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
        qs = TierStatus.objects.filter(
            status="approved"
        ).select_related("person", "programme")

        if programme_id:
            qs = qs.filter(programme_id=programme_id)
        if unit_id:
            qs = qs.filter(person__unit_id=unit_id)

        for ts in qs.order_by("-approved_at"):
            rows.append({
                "name": ts.person.full_name,
                "detail": f"{ts.get_tier_display()} in {ts.programme.name}",
                "when": ts.approved_at,
            })

    selected_programme = Programme.objects.filter(id=programme_id).first() if programme_id else None
    selected_unit = OrgUnit.objects.filter(id=unit_id).first() if unit_id else None

    return render(
        request,
        "reporting/drilldown.html",
        {
            "title": title,
            "rows": rows,
            "kpi": kpi,
            "selected_programme": selected_programme,
            "selected_unit": selected_unit,
        },
    )


@login_required
def memo_list(request):
    """List of all generated memos the user can see."""
    if not is_super_admin(request.user):
        try:
            roles = set(request.user.hub_account.roles.values_list("role", flat=True))
        except AttributeError:
            roles = set()
        if "executive_viewer" not in roles:
            messages.error(request, "You don't have permission to view memos.")
            return redirect("dashboard")

    memos = Memo.objects.all().select_related("created_by").order_by("-created_at")

    return render(
        request,
        "reporting/memo_list.html",
        {"memos": memos, "count": memos.count()},
    )


@login_required
def memo_generate(request):
    """Form to generate a new executive memo."""
    if not is_super_admin(request.user):
        try:
            roles = set(request.user.hub_account.roles.values_list("role", flat=True))
        except AttributeError:
            roles = set()
        if "executive_viewer" not in roles:
            messages.error(request, "You don't have permission to generate memos.")
            return redirect("dashboard")

    programmes = Programme.objects.filter(status="active").order_by("name")

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
            programme = Programme.objects.filter(id=programme_id).first()

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
def memo_detail(request, memo_id):
    """Read-only view of a generated memo."""
    if not is_super_admin(request.user):
        try:
            roles = set(request.user.hub_account.roles.values_list("role", flat=True))
        except AttributeError:
            roles = set()
        if "executive_viewer" not in roles:
            messages.error(request, "You don't have permission to view memos.")
            return redirect("dashboard")

    memo = get_object_or_404(Memo, id=memo_id)

    previous_versions = (
        Memo.objects.filter(
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
            "previous_versions": previous_versions,
        },
    )