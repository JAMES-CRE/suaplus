"""Passport views — the participant's own record, reached via tokenised link."""

import hashlib
import secrets
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.accounts.permissions import is_super_admin
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
from apps.passport.linking import issue_passport_link
from apps.passport.forms import CredentialSubmitForm
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
from apps.programmes.models import TierStatus
from apps.registry.models import Consent, Organisation, OrgUnit, PassportToken, Person
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
        Evidence.objects.filter(person=person, status="valid")
        .select_related("competency__domain", "level")
    )

    type_priority = {
        "external": 1, "artefact": 2, "assessor": 3,
        "task": 4, "attendance": 5, "self": 6,
    }

    # 1. Find the best evidence per competency
    best_per_competency = {}
    for ev in evidence:
        key = ev.competency_id
        existing = best_per_competency.get(key)
        if existing is None:
            best_per_competency[key] = ev
        else:
            if ev.level.ordinal > existing.level.ordinal:
                best_per_competency[key] = ev
            elif ev.level.ordinal == existing.level.ordinal and type_priority.get(ev.type, 99) < type_priority.get(existing.type, 99):
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
    """Check if the user has permission to review credentials."""
    if is_super_admin(user):
        return True
    try:
        roles = user.hub_account.roles.values_list("role", flat=True)
        return "programme_owner" in roles or "focal_person" in roles
    except AttributeError:
        return False


def _reviewable_credentials(user):
    """Return pending credentials within the reviewer's assigned scope."""
    qs = Credential.objects.filter(review_status__in=["submitted", "in_review"])
    if is_super_admin(user):
        return qs

    from apps.accounts.permissions import get_user_scopes

    scopes = get_user_scopes(user)
    if any(
        scope.role in ("focal_person", "programme_owner")
        and scope.scope_type == "all"
        for scope in scopes
    ):
        return qs

    unit_ids = {
        scope.scope_id
        for scope in scopes
        if scope.role == "focal_person"
        and scope.scope_type == "unit"
        and scope.scope_id is not None
    }
    programme_ids = {
        scope.scope_id
        for scope in scopes
        if scope.role == "programme_owner"
        and scope.scope_type == "programme"
        and scope.scope_id is not None
    }
    allowed = Q(pk__in=[])
    if unit_ids:
        allowed |= Q(person__unit_id__in=unit_ids)
    if programme_ids:
        allowed |= Q(
            catalogue__competencies__competency__programme_targets__programme_id__in=programme_ids
        )
    return qs.filter(allowed).distinct()


def _generate_evidence_from_credential(credential):
    """If the credential matches the catalogue, generate Evidence."""
    catalogue = CertificationCatalogue.objects.filter(
        issuer__iexact=credential.issuer or "",
        title__iexact=credential.title or "",
    ).first()

    if not catalogue:
        return

    mappings = CertificationCompetency.objects.filter(certification=catalogue)
    if not mappings.exists():
        return

    for mapping in mappings:
        level = Level.objects.filter(
            framework__is_knust=True,
            ordinal=mapping.max_level,
        ).first()
        if not level:
            continue
        Evidence.objects.create(
            person=credential.person,
            competency=mapping.competency,
            level=level,
            type="external",
            source_type="credential",
            credential=credential,
            status="valid",
            expires_at=credential.expires_at,
            framework_version=framework_version_label(mapping.competency.framework),
        )


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
    if not catalogue:
        return False

    mapping = catalogue.competencies.select_related("competency").first()
    if not mapping:
        return False

    level = Level.objects.filter(
        framework__is_knust=True, ordinal=mapping.max_level
    ).first()
    if not level:
        return False

    credential.catalogue = catalogue
    credential.badge_json = {
        "jwt": generate_badge_json(credential.person, mapping.competency, level)
    }
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
    credentials = Credential.objects.filter(person=person).order_by("-issued_at")

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

    current_comps = set(
        Evidence.objects.filter(person=person, status="valid")
        .values_list("competency_id", flat=True)
        .distinct()
    )

    programme_ids = tiers.values_list("programme_id", flat=True)
    target_comps = set(
        ProgrammeCompetency.objects.filter(programme_id__in=programme_ids)
        .values_list("competency_id", flat=True)
        .distinct()
    )

    gaps = target_comps - current_comps

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
        },
    )


@require_POST
def passport_goals(request, token):
    """Create or update a passport holder's personal learning goal."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(
        token_hash=token_hash, revoked_at__isnull=True
    ).select_related("person").first()
    if not pt:
        raise Http404("Passport not found")

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
def credential_submit(request):
    """A signed-in user submits an external certification."""
    try:
        person = request.user.hub_account.person
    except Exception:
        person = None
    if person is None:
        user = request.user
        name = (user.get_full_name() or user.username or user.email.split("@")[0]).strip()
        person = Person.objects.create(
            affiliation="external",
            email=user.email,
            full_name=name,
        )
        try:
            request.user.hub_account.person = person
            request.user.hub_account.save()
        except Exception:
            pass

    resubmission_id = request.GET.get("resubmit")
    resubmission = None
    if resubmission_id:
        resubmission = get_object_or_404(
            Credential,
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
def credential_review_queue(request):
    """List of credentials pending review, scoped to the reviewer's permissions."""
    from apps.accounts.permissions import get_user_scopes

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


@rate_limit("sas_start", max_requests=10, window_seconds=3600)
def sas_start(request):
    """Step 1: Enter KNUST email to start the self-assessment."""
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        if not email.endswith("@knust.edu.gh") and not email.endswith("@st.knust.edu.gh"):
            return render(request, "public/sas_start.html", {"error": "Please use a valid KNUST email address."})

        person, _ = Person.objects.get_or_create(
            email=email,
            defaults={"affiliation": "staff", "full_name": email.split("@")[0].title()},
        )

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        PassportToken.objects.create(person=person, token_hash=token_hash)

        url = f"{settings.SITE_URL}/sas/confirm/{raw_token}/"
        send_mail(
            "Your Sua+ Self-Assessment Link",
            f"Click here to start your self-assessment: {url}",
            settings.DEFAULT_FROM_EMAIL,
            [person.email],
        )
        return render(request, "public/sas_email_sent.html")

    return render(request, "public/sas_start.html")


def sas_confirm(request, token):
    """Step 2: Verify token, confirm profile, then show questionnaire."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(token_hash=token_hash, revoked_at__isnull=True).first()

    if not pt:
        return render(request, "public/invalid.html")

    person = pt.person

    # PHASE 1: PROFILE CONFIRMATION
    if not person.title or not person.category or (not person.unit_id and not person.unit_other):
        if request.method == "POST":
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
            return redirect("sas_confirm", token=token)

        units = OrgUnit.objects.filter(active=True, type__in=["department", "faculty", "school"]).order_by("name")
        return render(request, "public/sas_profile.html", {"person": person, "token": token, "units": units})

    # PHASE 2: QUESTIONNAIRE
    competencies = Competency.objects.filter(status="active").select_related("domain", "framework").order_by("domain__display_order", "code")

    if request.method == "POST":
        with transaction.atomic():
            template, _ = SurveyTemplate.objects.get_or_create(name="Baseline Self-Assessment", defaults={"type": "pre", "definition": {}})
            SelfAssessment.objects.create(person=person, template=template, status="submitted", submitted_at=timezone.now())

            for comp in competencies:
                level_val = request.POST.get(f"comp_{comp.id}")
                if level_val:
                    level = Level.objects.filter(framework__is_knust=True, ordinal=int(level_val)).first()
                    if level:
                        Evidence.objects.create(
                            person=person,
                            competency=comp,
                            level=level,
                            type="self",
                            source_type="self_assessment",
                            status="valid",
                            framework_version=framework_version_label(comp.framework),
                        )

            passport_url = issue_passport_link(person)

        send_notification(
            person=person,
            template_key="passport_link",
            context={"name": person.full_name, "session_title": "Self-Assessment", "passport_url": passport_url},
        )

        return render(request, "public/sas_done.html", {"passport_url": passport_url})

    return render(request, "public/sas_form.html", {"person": person, "competencies": competencies})


# ---------------------------------------------------------------------------
# Badge verification
# ---------------------------------------------------------------------------


def verify_badge_view(request, badge_jwt):
    """Public page to verify a badge signature."""
    badge_data = verify_badge(badge_jwt)

    if not badge_data:
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
            person = Person.objects.filter(email__iexact=email).first()
            if person:
                passport_url = issue_passport_link(person)

                send_notification(
                    person=person,
                    template_key="passport_link",
                    context={"name": person.full_name, "session_title": "Sua+ Portal", "passport_url": passport_url},
                )

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
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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


def passport_profile_edit(request, token):
    """Allow a passport owner to edit their profile."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(token_hash=token_hash, revoked_at__isnull=True).select_related("person").first()

    if not pt:
        raise Http404("Passport not found")

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
def my_passport(request):
    """Generate a fresh passport token for the logged-in user and redirect."""
    try:
        person = request.user.hub_account.person
    except Exception:
        messages.error(request, "Your account is not linked to a person record yet. Please contact the administrator.")
        return redirect("/hub/")

    passport_url = issue_passport_link(person)
    return redirect("passport", token=passport_url.rstrip("/").split("/")[-1])
