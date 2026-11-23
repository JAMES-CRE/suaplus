"""Passport views — the participant's own record, reached via tokenised link."""

import hashlib
import secrets
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.accounts.linking import resolve_person_for_user
from apps.accounts.permissions import (
    can,
    credential_queryset,
    is_super_admin,
    person_id_for,
    require_capability,
)
from apps.audit.services import log_event
from apps.core.rate_limit import rate_limit
from apps.library.models import (
    CertificationCatalogue,
    CertificationCompetency,
    Competency,
    Level,
    SurveyTemplate,
)
from apps.library.versioning import framework_version_label
from apps.notifications.services import (
    send_correction_request_to_facilitators,
    send_notification,
)
from apps.passport.forms import CredentialSubmitForm
from apps.passport.linking import issue_passport_link
from apps.passport.models import (
    CorrectionRequest,
    Credential,
    CredentialReview,
    Evidence,
    LearningGoal,
    PassportStampView,
    SelfAssessment,
)
from apps.passport.services import expire_due_records, generate_badge_json, verify_badge
from apps.passport.querysets import valid_evidence
from apps.programmes.models import TierStatus
from apps.registry.identity import (
    identity_data,
    match_identity,
    queue_identity,
    resolve_or_create,
)
from apps.registry.models import OrgUnit, PassportToken, Person
from apps.sessions.models import Attendance, Session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_email(email):
    """Mask an email like km@knust.edu.gh → k•@knust.edu.gh."""
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        masked = "•"
    else:
        masked = local[0] + "•" * (len(local) - 1)
    return f"{masked}@{domain}"


def _mask_phone(phone):
    """Mask a phone number to show only the first 3 and last 3 digits."""
    digits = (phone or "").replace(" ", "")
    if len(digits) <= 6:
        return "Not provided"
    return digits[:3] + "•" * (len(digits) - 6) + digits[-3:]


def _compute_competency_profile(person):
    """Group evidence by domain and calculate averages for self vs verified."""
    evidence = (
        valid_evidence().filter(person=person)
        .select_related("competency__domain", "level")
    )

    type_priority = {
        "external": 1, "artefact": 2, "assessor": 3,
        "task": 4, "attendance": 5, "self": 6,
    }

    # Keep the best self-rating and verified record independently for each skill.
    best_per_competency = {}
    for ev in evidence:
        key = (ev.competency_id, ev.type == "self")
        existing = best_per_competency.get(key)
        if existing is None:
            best_per_competency[key] = ev
        else:
            if ev.level.ordinal > existing.level.ordinal or ev.level.ordinal == existing.level.ordinal and type_priority.get(ev.type, 99) < type_priority.get(existing.type, 99):
                best_per_competency[key] = ev

    # 2. Group by domain and calculate stats
    domains = {}
    for ev in best_per_competency.values():
        domain = ev.competency.domain
        if domain not in domains:
            domains[domain] = {
                "competencies": [], 
                "self_total": 0, 
                "verified_total": 0, 
                "self_count": 0,
                "verified_count": 0
            }
        
        domains[domain]["competencies"].append(ev)
        
        # Tally up the scores
        if ev.type == "self":
            domains[domain]["self_total"] += ev.level.ordinal
            domains[domain]["self_count"] += 1
        else:
            domains[domain]["verified_total"] += ev.level.ordinal
            domains[domain]["verified_count"] += 1

    # 3. Calculate percentages
    for domain, data in domains.items():
        data["competencies"].sort(key=lambda e: e.competency.code)
        
        # Display score is the highest average of the two
        v_avg = data["verified_total"] / data["verified_count"] if data["verified_count"] > 0 else 0
        s_avg = data["self_total"] / data["self_count"] if data["self_count"] > 0 else 0
        
        data["display_score"] = round(max(v_avg, s_avg), 1)
        
        # Convert to percentages (max level is 5)
        data["self_percent"] = round((s_avg / 5) * 100, 1)
        data["verified_percent"] = round((v_avg / 5) * 100, 1)

    return domains


def _can_review(user):
    return can(user, "review_credentials")


def _reviewable_credentials(user):
    return credential_queryset(user).filter(review_status__in=["submitted", "in_review"])


def _require_passport_owner(request, person_id):
    if not is_super_admin(request.user) and (
        person_id_for(request.user) != person_id
        or not Person.objects.filter(pk=person_id, is_anonymised=False, merged_into__isnull=True).exists()
    ):
        raise PermissionDenied


def _generate_evidence_from_credential(credential):
    from apps.passport.awards import generate_credential_evidence
    return generate_credential_evidence(credential)


def _issue_badge_for_credential(credential):
    """Issue one verifiable badge when an approved credential has a catalogue map."""
    if credential.badge_json:
        return False

    catalogue = credential.catalogue
    if catalogue is None:
        catalogue = CertificationCatalogue.objects.filter(
            issuer__iexact=credential.issuer or "",
            title__iexact=credential.title or "",
            active=True,
        ).first()
    if not catalogue or not catalogue.active or credential.review_status != "approved" or credential.status != "valid":
        return False

    mapping = catalogue.competencies.select_related("competency").first()
    if not mapping:
        return False

    from apps.library.models import EvidenceRule
    from django.db.models import Q
    caps = EvidenceRule.objects.filter(Q(competency=mapping.competency) | Q(competency__isnull=True), evidence_type="external", status__in=["active", "published"]).values_list("max_level", flat=True)
    cap = min([mapping.max_level, *caps])
    level = Level.objects.filter(framework=mapping.competency.framework, ordinal=cap).first()
    if not level:
        return False

    credential.catalogue = catalogue
    signed = generate_badge_json(credential.person, mapping.competency, level)
    if not signed:
        return False
    credential.badge_json = {"jwt": signed}
    credential.save(update_fields=["catalogue", "badge_json", "updated_at"])
    return True


# ---------------------------------------------------------------------------
# Passport
# ---------------------------------------------------------------------------


def passport_view(request, token):
    """Render the passport for the person identified by the token."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = (
        PassportToken.objects.filter(token_hash=token_hash)
        .select_related("person")
        .first()
    )

    if not pt:
        raise Http404("Passport not found")
    if pt.revoked_at:
        raise Http404("This passport link is no longer valid")

    pt.last_used_at = timezone.now()
    pt.save(update_fields=["last_used_at"])

    person = pt.person
    expire_due_records()
    domains = _compute_competency_profile(person)

    # 1. Stamps
    stamps = (
        Attendance.objects.filter(
            person=person, status__in=["completed", "partial"]
        )
        .select_related("session__programme")
        .order_by("-check_in_at")[:20]
    )
    first_view_stamp_ids = set()
    for stamp in stamps:
        _view, created = PassportStampView.objects.get_or_create(
            person=person, attendance=stamp
        )
        if created:
            first_view_stamp_ids.add(stamp.id)

    # 2. Credentials
    credentials = list(Credential.objects.filter(person=person).order_by("-issued_at"))
    for credential in credentials:
        badge_jwt = (credential.badge_json or {}).get("jwt")
        if credential.review_status == "approved" and badge_jwt:
            credential.badge_share_url = request.build_absolute_uri(
                reverse("badge_verify", args=[badge_jwt])
            )

    # 3. Trainer Pathways (all programmes)
    from django.db.models import Max
    highest_tiers = (
        TierStatus.objects.filter(person=person)
        .values("programme")
        .annotate(max_tier=Max("tier"))
    )
    
    tier_ids = []
    for ht in highest_tiers:
        ts = TierStatus.objects.filter(
            person=person, programme=ht["programme"], tier=ht["max_tier"]
        ).first()
        if ts:
            tier_ids.append(ts.id)
            
    tiers = TierStatus.objects.filter(id__in=tier_ids).select_related("programme")

    # 4. Gap Analysis & Upcoming Sessions
    from apps.programmes.models import ProgrammeCompetency

    current_levels = {
        row["competency_id"]: row["highest_level"]
        for row in valid_evidence().filter(person=person)
        .values("competency_id")
        .annotate(highest_level=Max("level__ordinal"))
    }
    target_levels = {
        row["competency_id"]: row["target_level"]
        for row in ProgrammeCompetency.objects.filter(programme__status="active")
        .values("competency_id")
        .annotate(target_level=Max("target_level"))
    }
    gaps = {
        competency_id
        for competency_id, target_level in target_levels.items()
        if current_levels.get(competency_id, 0) < target_level
    }

    now = timezone.now()
    upcoming_sessions = (
        Session.objects.filter(
            start_at__gte=now,
            status__in=["scheduled", "open"]
        )
        .select_related("programme")
        .order_by("start_at")[:3]
    )

    recommendations = []
    for s in upcoming_sessions:
        session_comps = set(s.competencies.values_list("competency_id", flat=True))
        matching_gaps = gaps.intersection(session_comps)
        
        recommendations.append({
            "session": s,
            "is_recommended": len(matching_gaps) > 0,
            "raises": ", ".join(
                Competency.objects.filter(id__in=matching_gaps)
                .values_list("code", flat=True)
            ) if matching_gaps else ""
        })

    returned_credential = next(
        (credential for credential in credentials
         if credential.review_status == "returned"
         and credential.source == "self_submitted"),
        None,
    )
    recommended_session = next(
        (item for item in recommendations if item["is_recommended"]),
        recommendations[0] if recommendations else None,
    )
    if returned_credential:
        next_action = {
            "title": "Update your returned credential",
            "description": "A reviewer has asked for more information. Read their note and update your submission.",
            "url": f"{reverse('credential_submit')}?resubmit={returned_credential.id}",
            "label": "Update credential" if request.user.is_authenticated else "Sign in to update credential",
        }
    elif not domains and person.affiliation in {"staff", "student"}:
        next_action = {
            "title": "Start with your self-assessment",
            "description": "Rate your current skills to build your competency profile. Allow about 15 minutes.",
            "url": reverse("sas_start"),
            "label": "Start self-assessment",
        }
    elif recommended_session:
        next_action = {
            "title": "Explore your next workshop",
            "description": recommended_session["session"].title,
            "url": "#upcoming-sessions",
            "label": "View upcoming workshops",
        }
    else:
        next_action = {
            "title": "Choose your next learning goal",
            "description": "Set a skill you want to develop and give yourself a target date.",
            "url": "#learning-goals",
            "label": "Set a learning goal",
        }
    profile_summary = {
        "domain_count": len(domains),
        "verified_count": sum(data["verified_count"] for data in domains.values()),
        "credential_count": sum(
            credential.review_status == "approved" and credential.status == "valid"
            for credential in credentials
        ),
    }

    return render(
        request,
        "passport/passport.html",
        {
            "person": person,
            "masked_email": _mask_email(person.email),
            "masked_phone": _mask_phone(person.phone),
            "domains": domains,
            "stamps": stamps,
            "first_view_stamp_ids": first_view_stamp_ids,
            "stamp_count": stamps.count(),
            "credentials": credentials,
            "learning_goals": person.learning_goals.exclude(status="archived"),
            "tiers": tiers,
            "recommendations": recommendations,
            "token": token,
            "next_action": next_action,
            "profile_summary": profile_summary,
            "can_manage_passport": request.user.is_authenticated and (is_super_admin(request.user) or person_id_for(request.user) == person.pk),
        },
    )


@login_required
@require_POST
def passport_goals(request, token):
    """Create or update a passport holder's personal learning goal."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(
        token_hash=token_hash, revoked_at__isnull=True
    ).select_related("person").first()
    if not pt:
        raise Http404("Passport not found")

    _require_passport_owner(request, pt.person_id)
    action = request.POST.get("action", "add")
    if action == "add":
        title = request.POST.get("title", "").strip()
        target_date_text = request.POST.get("target_date", "").strip()
        target_date = parse_date(target_date_text) if target_date_text else None
        if not title:
            messages.error(request, "Enter a goal before saving it.")
        elif len(title) > 240:
            messages.error(request, "A goal must be 240 characters or fewer.")
        elif target_date_text and target_date is None:
            messages.error(request, "Enter a valid target date.")
        else:
            LearningGoal.objects.create(
                person=pt.person, title=title, target_date=target_date
            )
            messages.success(request, "Your learning goal has been added.")
    else:
        goal = get_object_or_404(
            LearningGoal, id=request.POST.get("goal_id"), person=pt.person
        )
        if action == "complete":
            goal.status = "completed"
            goal.completed_at = timezone.now()
            goal.save(update_fields=["status", "completed_at"])
            messages.success(request, "Goal marked complete.")
        elif action == "reopen":
            goal.status = "active"
            goal.completed_at = None
            goal.save(update_fields=["status", "completed_at"])
        elif action == "archive":
            goal.status = "archived"
            goal.save(update_fields=["status"])

    return redirect("passport", token=token)


def passport_invalid(request):
    """Shown when a passport token is invalid or revoked."""
    return render(request, "public/invalid.html")


# ---------------------------------------------------------------------------
# Credential submission and review
# ---------------------------------------------------------------------------


@login_required
@transaction.atomic
def credential_submit(request):
    """A signed-in user submits an external certification."""
    person = resolve_person_for_user(request.user, request=request)

    resubmission_id = request.GET.get("resubmit")
    resubmission = None
    if resubmission_id:
        resubmission = get_object_or_404(
            Credential.objects.select_for_update() if request.method == "POST" else Credential.objects.all(),
            id=resubmission_id,
            person=person,
            review_status="returned",
        )

    if request.method == "POST":
        form = CredentialSubmitForm(
            request.POST, request.FILES, instance=resubmission
        )
        if form.is_valid():
            credential = form.save(commit=False)
            credential.person = person
            if resubmission:
                credential.reviewer = None
                credential.reviewed_at = None
                credential.review_note = ""
            credential.catalogue = CertificationCatalogue.objects.filter(
                issuer__iexact=credential.issuer, title__iexact=credential.title, active=True,
            ).first()
            credential.save()

            cat = CertificationCatalogue.objects.filter(
                issuer__iexact=credential.issuer or "",
                title__iexact=credential.title or "",
                auto_approve=True,
                active=True,
            ).first()

            if cat:
                credential.catalogue = cat
                credential.review_status = "approved"
                credential.reviewed_at = timezone.now()
                credential.save()
                _generate_evidence_from_credential(credential)
                _issue_badge_for_credential(credential)
                send_notification(
                    credential.person,
                    "credential_approved",
                    {
                        "name": credential.person.full_name,
                        "credential_title": credential.title,
                        "passport_url": "/",
                    },
                )
                messages.success(
                    request, "Credential approved automatically."
                )
                passport_url = issue_passport_link(credential.person)
                return render(
                    request,
                    "passport/credential_submitted.html",
                    {
                        "auto_approved": True,
                        "credential": credential,
                        "passport_url": passport_url,
                    },
                )

            CredentialReview.objects.create(
                credential=credential,
                from_status="returned" if resubmission else "",
                to_status="submitted",
                actor=request.user,
                comment=(
                    "Resubmitted after providing requested information."
                    if resubmission
                    else "Submitted by user."
                ),
            )
            send_notification(
                person=credential.person,
                template_key="credential_submitted",
                context={
                    "name": credential.person.full_name,
                    "credential_title": credential.title,
                },
            )
            messages.success(
                request,
                "Credential resubmitted successfully. It will be reviewed shortly."
                if resubmission
                else "Credential submitted successfully. It will be reviewed shortly.",
            )
            passport_url = issue_passport_link(credential.person)
            return render(
                request,
                "passport/credential_submitted.html",
                {
                    "auto_approved": False,
                    "credential": credential,
                    "passport_url": passport_url,
                },
            )
    else:
        form = CredentialSubmitForm(instance=resubmission)

    return render(
        request,
        "passport/credential_submit.html",
        {"form": form, "resubmission": resubmission},
    )


@login_required
@require_capability("review_credentials")
def credential_review_queue(request):
    """List of credentials pending review, scoped to the reviewer's permissions."""

    if not _can_review(request.user):
        messages.error(request, "You don't have permission to review credentials.")
        return redirect("dashboard")

    qs = _reviewable_credentials(request.user).select_related("person").order_by("-created_at")

    return render(
        request,
        "credentials/review_queue.html",
        {"pending": qs, "count": qs.count()},
    )


@login_required
@require_capability("review_credentials")
def credential_review_detail(request, credential_id):
    """Review a specific credential and approve/reject it."""
    from django.conf import settings as django_settings
    from django.utils import timezone as tz

    if not _can_review(request.user):
        messages.error(request, "You don't have permission to review credentials.")
        return redirect("dashboard")

    credential = get_object_or_404(
        _reviewable_credentials(request.user).select_related("person"),
        id=credential_id,
    )

    try:
        reviewer_person_id = request.user.hub_account.person_id
    except AttributeError:
        reviewer_person_id = None
    if request.method == "POST" and credential.person_id == reviewer_person_id:
        messages.error(request, "You cannot review your own credential submission.")
        return redirect("credential_review_queue")

    from datetime import date
    today = date.today()

    person_name = (credential.person.full_name or "").lower().strip()
    credential_title = (credential.title or "").lower()
    name_tokens = [t for t in person_name.split() if len(t) > 2]
    name_matches = all(t in credential_title for t in name_tokens) if name_tokens else False

    id_reused = False
    if credential.credential_id:
        id_reused = Credential.objects.filter(credential_id=credential.credential_id).exclude(id=credential.id).exists()

    has_url = bool(credential.verification_url)

    dates_valid = True
    if credential.issued_at and credential.issued_at > today:
        dates_valid = False
    if credential.expires_at and credential.expires_at < today:
        dates_valid = False

    checks = {
        "name_matches": name_matches,
        "id_unique": not id_reused,
        "has_url": has_url,
        "dates_valid": dates_valid,
    }

    if request.method == "POST":
        decision = request.POST.get("decision")
        comment = request.POST.get("comment", "").strip()

        if decision not in ("in_review", "approved", "returned", "rejected"):
            messages.error(request, "Invalid decision.")
            return redirect("credential_review_detail", credential_id=credential.id)
        if decision == "returned" and not comment:
            messages.error(
                request,
                "Add a comment explaining what information is needed before returning the credential.",
            )
            return redirect("credential_review_detail", credential_id=credential.id)

        with transaction.atomic():
            credential = Credential.objects.select_for_update().get(pk=credential.pk)
            if credential.review_status not in {"submitted", "in_review"}:
                messages.info(request, "This credential has already been reviewed.")
                return redirect("credential_review_queue")
            from_status = credential.review_status
            credential.review_status = decision
            credential.reviewer = request.user
            credential.reviewed_at = tz.now()
            credential.review_note = comment
            credential.save()

            log_event(
                request=request,
                action=f"credential_{decision}",
                entity="Credential",
                entity_id=credential.id,
                before={"status": from_status},
                after={"status": decision},
            )
            CredentialReview.objects.create(
                credential=credential,
                from_status=from_status,
                to_status=decision,
                actor=request.user,
                comment=comment,
            )

            if decision == "in_review":
                send_notification(
                    person=credential.person,
                    template_key="credential_in_review",
                    context={
                        "name": credential.person.full_name,
                        "credential_title": credential.title,
                    },
                )
                messages.info(request, f"Started review for {credential.person.full_name}.")
            elif decision == "approved":
                _generate_evidence_from_credential(credential)
                _issue_badge_for_credential(credential)

                send_notification(
                    person=credential.person,
                    template_key="credential_approved",
                    context={
                        "name": credential.person.full_name,
                        "credential_title": credential.title,
                        "passport_url": f"{django_settings.SITE_URL}/",
                    },
                )
                messages.success(request, f"Approved credential for {credential.person.full_name}.")
            elif decision == "returned":
                send_notification(
                    person=credential.person,
                    template_key="credential_returned",
                    context={
                        "name": credential.person.full_name,
                        "credential_title": credential.title,
                        "review_note": comment,
                    },
                )
                messages.info(
                    request,
                    f"Returned credential for {credential.person.full_name} for more information.",
                )
            else:
                send_notification(
                    person=credential.person,
                    template_key="credential_rejected",
                    context={
                        "name": credential.person.full_name,
                        "credential_title": credential.title,
                        "review_note": comment,
                    },
                )
                messages.info(request, f"Rejected credential for {credential.person.full_name}.")

        return redirect("credential_review_queue")

    return render(
        request,
        "credentials/review_detail.html",
        {"credential": credential, "checks": checks},
    )


# ---------------------------------------------------------------------------
# Self-assessment (SAS)
# ---------------------------------------------------------------------------


from apps.passport.sas_views import send_self_assessment_link, sas_start, sas_confirm


# ---------------------------------------------------------------------------
# Badge verification
# ---------------------------------------------------------------------------


def verify_badge_view(request, badge_jwt):
    """Public page to verify a badge signature."""
    badge_data = verify_badge(badge_jwt)

    if not badge_data:
        return render(request, "public/badge_invalid.html")
    credential = Credential.objects.filter(badge_json__jwt=badge_jwt).first()
    if credential and (credential.status != "valid" or credential.review_status != "approved" or (credential.expires_at and credential.expires_at < timezone.localdate()) or credential.person.is_anonymised or credential.person.merged_into_id):
        return render(request, "public/badge_invalid.html")

    return render(request, "public/badge_valid.html", {"badge": badge_data})


# ---------------------------------------------------------------------------
# Resend passport link
# ---------------------------------------------------------------------------


@rate_limit("resend", max_requests=10, window_seconds=3600)
def resend_link(request):
    """Public page to request a new passport link."""
    message = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()

        if email:
            email_digest = hashlib.sha256(email.encode()).hexdigest()
            email_cache_key = f"rl:resend:email:{email_digest}"
            within_email_limit = cache.add(
                email_cache_key, 1, timeout=3600
            )
            if not within_email_limit:
                try:
                    within_email_limit = cache.incr(email_cache_key) <= 3
                except ValueError:
                    within_email_limit = cache.add(
                        email_cache_key, 1, timeout=3600
                    )

        else:
            within_email_limit = False

        if email and within_email_limit:
            try:
                result = match_identity(identity_data(email=email), source="resend")
            except ValidationError:
                result = None
            if result and result.status == "matched":
                person = result.person
                passport_url = issue_passport_link(person)
                send_notification(person=person, template_key="passport_link", context={"name": person.full_name, "session_title": "Sua+ Portal", "passport_url": passport_url})
            elif result and result.status == "conflict":
                queue_identity(result, source="resend")

        message = "If that email is in our system, a new passport link has been sent to it."

    return render(request, "public/resend.html", {"message": message})


# ---------------------------------------------------------------------------
# Report an error
# ---------------------------------------------------------------------------


@rate_limit("report_error", max_requests=5, window_seconds=3600)
def report_error(request, token):
    """Passport holder reports an error with their record."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(token_hash=token_hash, revoked_at__isnull=True).first()

    if not pt:
        return render(request, "public/invalid.html")

    person = pt.person

    stamps = Attendance.objects.filter(
        person=person, status__in=["completed", "partial"]
    ).select_related("session__programme").order_by("-check_in_at")

    if request.method == "POST":
        message = request.POST.get("message", "").strip()
        if message:
            session_id = request.POST.get("session", "").strip()
            session = stamps.filter(session_id=session_id).values_list(
                "session", flat=True
            ).first() if session_id else None
            correction = CorrectionRequest.objects.create(
                person=person, session_id=session, message=message
            )
            if correction.session_id:
                send_correction_request_to_facilitators(correction)
            return render(request, "public/error_reported.html")

    return render(
        request,
        "public/report_error.html",
        {"person": person, "token": token, "stamps": stamps},
    )


def passport_transcript(request, token):
    """Printable transcript of the passport."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(token_hash=token_hash).select_related("person").first()

    if not pt or pt.revoked_at:
        raise Http404("Passport not found")

    expire_due_records()
    person = pt.person
    domains = _compute_competency_profile(person)
    credentials = Credential.objects.filter(person=person, status="valid")
    tiers = TierStatus.objects.filter(person=person).select_related("programme")
    stamps = Attendance.objects.filter(person=person, status__in=["completed", "partial"]).select_related("session__programme").order_by("-check_in_at")[:50]

    return render(
        request,
        "passport/transcript.html",
        {
            "person": person,
            "domains": domains,
            "credentials": credentials,
            "tiers": tiers,
            "stamps": stamps,
            "stamp_count": stamps.count(),
            "masked_email": _mask_email(person.email),
            "printed_at": timezone.now(),
            "token": token,
        },
    )


def passport_transcript_pdf(request, token):
    """Download a self-contained PDF version of a participant transcript."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(
        token_hash=token_hash, revoked_at__isnull=True
    ).select_related("person").first()
    if not pt:
        raise Http404("Passport not found")

    expire_due_records()
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
    )

    person = pt.person
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.6 * cm,
        leftMargin=1.6 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Sua+ Passport Transcript", styles["Title"]),
        Paragraph(person.full_name, styles["Heading2"]),
        Paragraph(
            f"Generated {timezone.localtime().strftime('%d %b %Y, %H:%M')}",
            styles["Normal"],
        ),
        Spacer(1, 0.45 * cm),
    ]

    domains = _compute_competency_profile(person)
    story.append(Paragraph("Competency Profile", styles["Heading2"]))
    if domains:
        for domain, data in domains.items():
            story.append(Paragraph(domain.name, styles["Heading3"]))
            rows = [["Code", "Competency", "Level", "Evidence"]]
            rows.extend(
                [
                    evidence.competency.code,
                    evidence.competency.title,
                    f"{evidence.level.ordinal} - {evidence.level.label}",
                    evidence.get_type_display(),
                ]
                for evidence in data["competencies"]
            )
            table = Table(rows, colWidths=[2.1 * cm, 7.1 * cm, 3.0 * cm, 4.0 * cm], repeatRows=1)
            table.setStyle(_transcript_table_style())
            story.extend([table, Spacer(1, 0.35 * cm)])
    else:
        story.append(Paragraph("No competencies recorded.", styles["Normal"]))

    credentials = Credential.objects.filter(person=person, status="valid").order_by("-issued_at")
    story.extend([Spacer(1, 0.25 * cm), Paragraph("Credentials", styles["Heading2"])])
    credential_rows = [["Title", "Issuer", "Issued", "Status"]]
    credential_rows.extend(
        [credential.title, credential.issuer or "-", credential.issued_at.strftime("%d %b %Y"), credential.get_status_display()]
        for credential in credentials
    )
    if len(credential_rows) == 1:
        story.append(Paragraph("No credentials recorded.", styles["Normal"]))
    else:
        table = Table(credential_rows, colWidths=[6.0 * cm, 5.0 * cm, 3.2 * cm, 2.0 * cm], repeatRows=1)
        table.setStyle(_transcript_table_style())
        story.append(table)

    stamps = Attendance.objects.filter(
        person=person, status__in=["completed", "partial"]
    ).select_related("session__programme").order_by("-check_in_at")[:50]
    story.extend([Spacer(1, 0.45 * cm), Paragraph("Workshop Stamps", styles["Heading2"])])
    stamp_rows = [["Date", "Session", "Programme", "Status"]]
    stamp_rows.extend(
        [
            attendance.check_in_at.strftime("%d %b %Y") if attendance.check_in_at else "-",
            attendance.session.title,
            attendance.session.programme.name,
            attendance.get_status_display(),
        ]
        for attendance in stamps
    )
    if len(stamp_rows) == 1:
        story.append(Paragraph("No stamps recorded.", styles["Normal"]))
    else:
        table = Table(stamp_rows, colWidths=[3.0 * cm, 6.2 * cm, 5.0 * cm, 2.0 * cm], repeatRows=1)
        table.setStyle(_transcript_table_style())
        story.append(table)

    document.build(story)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="suaplus-transcript-{person.id}.pdf"'
    return response


def _transcript_table_style():
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle

    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1A1A1A")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
    )


def passport_profile(request, token):
    """View a passport owner's profile details."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(token_hash=token_hash, revoked_at__isnull=True).select_related("person").first()

    if not pt:
        raise Http404("Passport not found")

    person = pt.person

    return render(
        request,
        "passport/profile.html",
        {
            "person": person,
            "token": token,
            "masked_email": _mask_email(person.email),
            "masked_phone": _mask_phone(person.phone),
        },
    )


@login_required
def passport_profile_edit(request, token):
    """Allow a passport owner to edit their profile."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(token_hash=token_hash, revoked_at__isnull=True).select_related("person").first()

    if not pt:
        raise Http404("Passport not found")

    _require_passport_owner(request, pt.person_id)
    person = pt.person

    if request.method == "POST":
        person.full_name = request.POST.get("full_name", person.full_name).strip()
        person.title = request.POST.get("title", "").strip()
        person.category = request.POST.get("category", "").strip()
        person.phone = request.POST.get("phone", "").strip()

        selected_unit = request.POST.get("unit", "")
        if selected_unit == "other":
            person.unit_id = None
            person.unit_other = request.POST.get("unit_other", "").strip()
        elif selected_unit:
            person.unit_id = int(selected_unit)
            person.unit_other = ""
        else:
            person.unit_id = None
            person.unit_other = ""

        person.save()
        messages.success(request, "Your profile has been updated.")
        return redirect("passport_profile", token=token)

    units = OrgUnit.objects.filter(active=True, type__in=["department", "faculty", "school"]).order_by("name")

    return render(
        request,
        "passport/profile_edit.html",
        {
            "person": person,
            "token": token,
            "units": units,
            "masked_email": _mask_email(person.email),
        },
    )


@login_required
def credential_file(request, credential_id):
    """Serve certificates through an authorised route, never a public media URL."""
    from django.http import FileResponse
    from django.utils.cache import patch_cache_control

    credential = get_object_or_404(Credential.objects.select_related("person"), pk=credential_id)
    own_record = credential.person_id == person_id_for(request.user) and not credential.person.is_anonymised
    scoped_reader = credential_queryset(request.user, "view_people").filter(pk=credential.pk).exists()
    if not own_record and not scoped_reader:
        raise Http404("Certificate not found")
    if not credential.file:
        raise Http404("Certificate not found")
    try:
        response = FileResponse(credential.file.open("rb"), as_attachment=True)
    except FileNotFoundError as exc:
        raise Http404("Certificate not found") from exc
    patch_cache_control(response, private=True, no_store=True)
    return response


@login_required
def my_passport(request):
    """Generate a fresh passport token for the logged-in user and redirect."""
    person = resolve_person_for_user(request.user, request=request)
    if person is None:
        messages.error(request, "Your account is not linked to a person record yet. Please contact the administrator.")
        return redirect("/hub/")

    passport_url = issue_passport_link(person)
    return redirect("passport", token=passport_url.rstrip("/").split("/")[-1])
