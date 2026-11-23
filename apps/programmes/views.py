"""Programmes views - Scout list, nominations, partner reports, recognition."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.permissions import (
    is_programme_owner,
    require_capability,
    scope_records,
    trainer_status_queryset,
)
from apps.notifications.services import send_notification
from apps.programmes.models import (
    Nomination,
    Partner,
    Programme,
    TierCriterion,
    TierStatus,
)
from apps.registry.models import Organisation
from apps.reporting.access import scoped_reporting
from apps.reporting.services.kpis import suppress
from apps.sessions.models import Attendance, Facilitation, Session, SurveyResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_nominate(user, tier_status):
    return trainer_status_queryset(user).filter(pk=tier_status.pk).exists()


def _can_approve_programme(user, programme_id):
    return is_programme_owner(user, programme_id)


# ---------------------------------------------------------------------------
# Scout list
# ---------------------------------------------------------------------------


@login_required
@require_capability("nominate_trainers")
def scout_list(request):
    """A union of the nominator's programme/unit/session scopes."""
    qs = trainer_status_queryset(request.user).select_related("person", "programme").filter(status="eligible")

    # Optional filters
    programme_id = request.GET.get("programme")
    tier = request.GET.get("tier")
    if programme_id:
        qs = qs.filter(programme_id=programme_id)
    if tier:
        qs = qs.filter(tier=tier)

    qs = qs.order_by("programme__name", "tier", "person__full_name")

    programmes = scope_records(Programme.objects.filter(status="active"), request.user, "nominate_trainers").order_by("name")

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
@require_capability("nominate_trainers")
def nominate(request, tier_status_id):
    """Create a Nomination for a TierStatus."""
    ts = get_object_or_404(
        trainer_status_queryset(request.user).select_related("person", "programme"),
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
@require_capability("nominate_trainers")
def nomination_list(request):
    qs = scope_records(Nomination.objects.select_related("person", "programme"), request.user, "nominate_trainers").filter(decision="pending")

    qs = qs.order_by("-created_at")

    return render(
        request,
        "programmes/nomination_list.html",
        {"nominations": qs, "count": qs.count()},
    )


@login_required
@require_capability("nominate_trainers")
def nomination_detail(request, nomination_id):
    """Review and approve/reject one nomination."""
    nomination = get_object_or_404(
        scope_records(Nomination.objects.select_related("person", "programme"), request.user, "nominate_trainers"),
        id=nomination_id,
    )

    if request.method == "POST" and not _can_approve_programme(request.user, nomination.programme_id):
        raise PermissionDenied

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
                if nomination.person.email and nomination.tier >= 3:
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
                    if account.person_id is None:
                        if UserAccount.objects.filter(person=nomination.person).exclude(pk=account.pk).exists():
                            raise PermissionDenied("Reconcile the participant's accounts before promotion.")
                        account.person = nomination.person
                        account.save(update_fields=["person"])
                    elif account.person_id != nomination.person_id:
                        raise PermissionDenied("The login account represents a different participant.")
                    RoleAssignment.objects.get_or_create(
                        user_account=account,
                        role="associate_trainer" if nomination.tier == 3 else "facilitator",
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
@scoped_reporting("export_reports")
def partner_report_list(request):
    """Partners and organisations covered by the caller's report scopes."""
    partner_ids = scope_records(Partner.objects.all(), request.user, "export_reports").values("pk")
    org_ids = scope_records(Organisation.objects.all(), request.user, "export_reports").values("pk")
    organisations = Organisation.objects.filter(Q(pk__in=org_ids) | Q(partnerships__pk__in=partner_ids)).annotate(
        programme_count=Count("partnerships__programmes", distinct=True)
    ).distinct().order_by("name")

    return render(
        request,
        "programmes/partner_report_list.html",
        {"organisations": organisations, "count": organisations.count()},
    )


@login_required
@scoped_reporting("export_reports")
def partner_report(request, org_id):
    partner_ids = scope_records(Partner.objects.all(), request.user, "export_reports").values("pk")
    org_ids = scope_records(Organisation.objects.all(), request.user, "export_reports").values("pk")
    allowed_orgs = Organisation.objects.filter(Q(pk__in=org_ids) | Q(partnerships__pk__in=partner_ids)).distinct()
    org = get_object_or_404(allowed_orgs, id=org_id)

    attendances = scope_records(Attendance.objects.all(), request.user, "export_reports").filter(
        Q(person__organisation=org) | Q(session__programme__partners__partner__organisation=org) | Q(session__partners__partner__organisation=org),
        status__in=["completed", "partial"],
    ).select_related("session__programme", "person").distinct()

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
@require_capability("view_people")
def recognition_list(request):
    """Public recognition list of certified trainers, opt-in."""
    trainers = (
        scope_records(TierStatus.objects.all(), request.user, "view_people").filter(
            status="approved",
            tier__gte=4,
            person__age_band="18_plus",
        )
        .select_related("person", "programme")
        .order_by("programme__name", "person__full_name")
    )

    for ts in trainers:
        ts.session_count = scope_records(Facilitation.objects.all(), request.user, "view_people").filter(
            person=ts.person, session__programme=ts.programme
        ).count()

        direct = (
            scope_records(Facilitation.objects.all(), request.user, "view_people").filter(
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
                scope_records(Facilitation.objects.all(), request.user, "view_people").filter(
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
