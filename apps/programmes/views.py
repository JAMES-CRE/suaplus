"""Programmes views - Scout list, nominations, partner reports, recognition."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.permissions import get_user_scopes, is_super_admin
from apps.notifications.services import send_notification
from apps.passport.models import Evidence
from apps.programmes.models import (
    Nomination,
    Programme,
    TierCriterion,
    TierStatus,
)
from apps.registry.models import Organisation
from apps.reporting.services.kpis import suppress
from apps.sessions.models import Attendance, Facilitation, Session, SurveyResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_nominate(user, tier_status):
    """Programme owners for the tier's programme, or super admins, can nominate."""
    if is_super_admin(user):
        return True
    try:
        account = user.hub_account
    except AttributeError:
        return False
    return RoleAssignment.objects.filter(
        user_account=account,
        role="programme_owner",
        scope_type="programme",
        scope_id=tier_status.programme_id,
    ).exists()


def _can_approve_programme(user, programme_id):
    """Can this user approve nominations for this programme?"""
    if is_super_admin(user):
        return True
    try:
        account = user.hub_account
    except AttributeError:
        return False
    return RoleAssignment.objects.filter(
        user_account=account,
        role="programme_owner",
        scope_type="programme",
        scope_id=programme_id,
    ).exists()


# ---------------------------------------------------------------------------
# Scout list
# ---------------------------------------------------------------------------


@login_required
def scout_list(request):
    """People eligible for a trainer tier, scoped to the user's permissions."""
    qs = TierStatus.objects.select_related("person", "programme").filter(
        status="eligible"
    )

    # Scope filtering
    if not is_super_admin(request.user):
        scopes = get_user_scopes(request.user)
        programme_ids = {
            s.scope_id
            for s in scopes
            if s.role == "programme_owner" and s.scope_type == "programme"
        }
        unit_ids = {
            s.scope_id
            for s in scopes
            if s.role == "focal_person" and s.scope_type == "unit"
        }
        if programme_ids:
            qs = qs.filter(programme_id__in=programme_ids)
        elif unit_ids:
            qs = qs.filter(person__unit_id__in=unit_ids)
        else:
            qs = qs.none()

    # Optional filters
    programme_id = request.GET.get("programme")
    tier = request.GET.get("tier")
    if programme_id:
        qs = qs.filter(programme_id=programme_id)
    if tier:
        qs = qs.filter(tier=tier)

    qs = qs.order_by("programme__name", "tier", "person__full_name")

    programmes = Programme.objects.filter(status="active").order_by("name")

    return render(
        request,
        "programmes/scout_list.html",
        {
            "statuses": qs,
            "programmes": programmes,
            "tier_choices": TierCriterion.TIER_CHOICES,
            "selected_programme": programme_id or "",
            "selected_tier": tier or "",
            "count": qs.count(),
        },
    )


# ---------------------------------------------------------------------------
# Nominations
# ---------------------------------------------------------------------------


@login_required
def nominate(request, tier_status_id):
    """Create a Nomination for a TierStatus."""
    ts = get_object_or_404(
        TierStatus.objects.select_related("person", "programme"),
        id=tier_status_id,
    )

    if not _can_nominate(request.user, ts):
        messages.error(
            request, "You don't have permission to nominate for this programme."
        )
        return redirect("scout_list")

    if request.method == "POST":
        comments = request.POST.get("comments", "").strip()
        with transaction.atomic():
            Nomination.objects.create(
                person=ts.person,
                programme=ts.programme,
                tier=ts.tier,
                nominated_by=request.user,
                decision="pending",
                comments=comments,
            )
            ts.status = "nominated"
            ts.save(update_fields=["status"])

            send_notification(
                person=ts.person,
                template_key="nomination_pending",
                context={
                    "nominator_name": request.user.get_full_name()
                    or request.user.username,
                    "person_name": ts.person.full_name,
                    "tier": ts.get_tier_display(),
                    "programme_name": ts.programme.name,
                    "review_url": f"{settings.SITE_URL}/nominations/",
                },
            )

        messages.success(
            request,
            f"Nominated {ts.person.full_name} for tier {ts.tier} "
            f"in {ts.programme.name}.",
        )
        return redirect("scout_list")

    return render(
        request,
        "programmes/nominate.html",
        {"tier_status": ts},
    )


@login_required
def nomination_list(request):
    """Pending nominations the user can approve."""
    qs = Nomination.objects.select_related("person", "programme").filter(
        decision="pending"
    )

    if not is_super_admin(request.user):
        try:
            account = request.user.hub_account
        except AttributeError:
            qs = qs.none()
            account = None
        if account:
            programme_ids = RoleAssignment.objects.filter(
                user_account=account,
                role="programme_owner",
                scope_type="programme",
            ).values_list("scope_id", flat=True)
            qs = qs.filter(programme_id__in=programme_ids)

    qs = qs.order_by("-created_at")

    return render(
        request,
        "programmes/nomination_list.html",
        {"nominations": qs, "count": qs.count()},
    )


@login_required
def nomination_detail(request, nomination_id):
    """Review and approve/reject one nomination."""
    nomination = get_object_or_404(
        Nomination.objects.select_related("person", "programme"),
        id=nomination_id,
    )

    if not _can_approve_programme(request.user, nomination.programme_id):
        messages.error(
            request, "You don't have permission to approve this nomination."
        )
        return redirect("nomination_list")

    if request.method == "POST":
        decision = request.POST.get("decision")
        comment = request.POST.get("comment", "").strip()

        if decision not in ("approved", "rejected"):
            messages.error(request, "Invalid decision.")
            return redirect("nomination_detail", nomination_id=nomination.id)

        with transaction.atomic():
            nomination.decision = decision
            nomination.decided_by = request.user
            nomination.decided_at = timezone.now()
            if comment:
                nomination.comments = (
                    (nomination.comments + "\n---\n" + comment).strip()
                )
            nomination.save()

            tier_status = TierStatus.objects.filter(
                person=nomination.person,
                programme=nomination.programme,
                tier=nomination.tier,
            ).first()

            if decision == "approved":
                if tier_status:
                    tier_status.status = "approved"
                    tier_status.approved_by = request.user
                    tier_status.approved_at = timezone.now()
                    tier_status.save(
                        update_fields=["status", "approved_by", "approved_at"]
                    )

                # Grant facilitator role scoped to the programme.
                # Create the User if it doesn't exist so the person can
                # actually log in via Google or OTP.
                if nomination.person.email:
                    User = get_user_model()
                    auth_user = User.objects.filter(
                        email__iexact=nomination.person.email
                    ).first()

                    if not auth_user:
                        auth_user = User.objects.create_user(
                            username=nomination.person.email,
                            email=nomination.person.email,
                            password=None,
                        )

                    auth_method = (
                        "google"
                        if nomination.person.affiliation in ("staff", "student")
                        else "otp"
                    )

                    account, _ = UserAccount.objects.get_or_create(
                        user=auth_user,
                        defaults={
                            "person": nomination.person,
                            "auth_method": auth_method,
                        },
                    )
                    RoleAssignment.objects.get_or_create(
                        user_account=account,
                        role="facilitator",
                        scope_type="programme",
                        scope_id=nomination.programme_id,
                    )

                send_notification(
                    person=nomination.person,
                    template_key="nomination_approved",
                    context={
                        "name": nomination.person.full_name,
                        "tier": nomination.get_tier_display(),
                        "programme_name": nomination.programme.name,
                        "login_url": f"{settings.SITE_URL}/admin/",
                    },
                )

                messages.success(
                    request,
                    f"Approved {nomination.person.full_name} as "
                    f"{nomination.get_tier_display()} in {nomination.programme.name}.",
                )
            else:
                if tier_status:
                    tier_status.status = "eligible"
                    tier_status.save(update_fields=["status"])
                messages.info(
                    request,
                    f"Rejected {nomination.person.full_name}'s nomination.",
                )

        return redirect("nomination_list")

    # ---- GET branch (was unreachable before, now runs correctly) ----

    person_has_account = False
    if nomination.person.email:
        person_has_account = UserAccount.objects.filter(
            user__email__iexact=nomination.person.email
        ).exists()

    tier_status = TierStatus.objects.filter(
        person=nomination.person,
        programme=nomination.programme,
        tier=nomination.tier,
    ).first()

    criteria_list = []
    if tier_status and tier_status.progressed_criteria:
        criteria_list = tier_status.progressed_criteria

    return render(
        request,
        "programmes/nomination_detail.html",
        {
            "nomination": nomination,
            "person_has_account": person_has_account,
            "criteria_list": criteria_list,
        },
    )


# ---------------------------------------------------------------------------
# Partner reports
# ---------------------------------------------------------------------------


@login_required
def partner_report_list(request):
    """List all partner organisations with linked programmes."""
    if not is_super_admin(request.user):
        try:
            roles = set(
                request.user.hub_account.roles.values_list("role", flat=True)
            )
        except AttributeError:
            roles = set()
        if "executive_viewer" not in roles:
            messages.error(
                request, "You don't have permission to view partner reports."
            )
            return redirect("hub_home")

    organisations = (
        Organisation.objects.annotate(
            programme_count=Count("partnerships__programmes", distinct=True)
        )
        .filter(programme_count__gt=0)
        .order_by("name")
    )

    return render(
        request,
        "programmes/partner_report_list.html",
        {"organisations": organisations, "count": organisations.count()},
    )


@login_required
def partner_report(request, org_id):
    """Report for one partner organisation."""
    if not is_super_admin(request.user):
        try:
            roles = set(
                request.user.hub_account.roles.values_list("role", flat=True)
            )
        except AttributeError:
            roles = set()
        if "executive_viewer" not in roles:
            messages.error(
                request, "You don't have permission to view partner reports."
            )
            return redirect("hub_home")

    org = get_object_or_404(Organisation, id=org_id)

    attendances = Attendance.objects.filter(
        person__organisation=org,
        status__in=["completed", "partial"],
    ).select_related("session__programme", "person")

    reach = attendances.values("person_id").distinct().count()

    by_programme = (
        attendances.values("session__programme__name")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("-people")
    )

    csat = SurveyResponse.objects.filter(
        attendance__in=attendances, type="exit", csat__isnull=False
    ).aggregate(mean=Avg("csat"))["mean"]

    sessions = (
        Session.objects.filter(attendances__in=attendances)
        .distinct()
        .order_by("-start_at")[:20]
    )

    return render(
        request,
        "programmes/partner_report.html",
        {
            "organisation": org,
            "reach": reach,
            "reach_display": suppress(reach),
            "by_programme": by_programme,
            "csat_mean": round(csat, 2) if csat else None,
            "sessions": sessions,
        },
    )


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------


@login_required
def recognition_list(request):
    """Public recognition list of certified trainers, opt-in."""
    trainers = (
        TierStatus.objects.filter(status="approved", tier__gte=4)
        .select_related("person", "programme")
        .order_by("programme__name", "person__full_name")
    )

    for ts in trainers:
        ts.session_count = Facilitation.objects.filter(
            person=ts.person, session__programme=ts.programme
        ).count()

        direct = (
            Facilitation.objects.filter(
                person=ts.person, session__programme=ts.programme
            )
            .values_list("session__attendances__person_id", flat=True)
            .distinct()
        )
        direct_ids = [d for d in direct if d and d != ts.person_id]
        ts.direct_count = len(direct_ids)

        second = 0
        if direct_ids:
            second = (
                Facilitation.objects.filter(
                    person_id__in=direct_ids,
                    session__programme=ts.programme,
                )
                .values_list("session__attendances__person_id", flat=True)
                .distinct()
                .exclude(person_id__in=direct_ids)
                .exclude(person_id=ts.person_id)
                .count()
            )

        ts.cascade_multiplier = (
            round((ts.direct_count + second) / ts.direct_count, 2)
            if ts.direct_count
            else 0
        )

    return render(
        request,
        "programmes/recognition.html",
        {"trainers": trainers},
    )