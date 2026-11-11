"""Passport views — the participant's own record, reached via tokenised link."""

import hashlib
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from apps.reporting.services.kpis import get_recommended_sessions
from apps.sessions.models import Session
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
from apps.notifications.services import send_notification
from apps.passport.forms import CredentialSubmitForm
from apps.passport.models import (
    CorrectionRequest,
    Credential,
    CredentialReview,
    Evidence,
    SelfAssessment,
)
from apps.passport.services import generate_badge_json, verify_badge
from apps.programmes.models import TierStatus
from apps.registry.models import Consent, Organisation, OrgUnit, PassportToken, Person
from apps.sessions.models import Attendance, Session



# Helpers



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




def _mask_phone(phone):
    """Mask a phone like +233244123456 → +233••••3456."""
    if not phone:
        return phone
    if len(phone) <= 4:
        return "•" * len(phone)
    return phone[:4] + "•" * (len(phone) - 8) + phone[-4:]


def _create_passport_link(person):
    """Generate a fresh passport token and return the full URL."""
    raw_token = secrets.token_urlsafe(32)
    PassportToken.objects.create(
        person=person,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
    )
    return f"{settings.SITE_URL}/p/{raw_token}/"


def _compute_competency_profile(person):
    """Group evidence by domain and calculate averages for self vs verified."""
    from apps.passport.models import Evidence

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
        # If they have no self-rated, it's 0%. If they have level 3, it's 60%.
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
        )



# Passport
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
    domains = _compute_competency_profile(person)

    # 1. Stamps
    stamps = (
        Attendance.objects.filter(
            person=person, status__in=["completed", "partial"]
        )
        .select_related("session__programme")
        .order_by("-check_in_at")[:20]
    )

    # 2. Credentials
    credentials = Credential.objects.filter(
        person=person, status="valid"
    ).order_by("-issued_at")

    # 3. Trainer Pathways (all programmes)
    from django.db.models import Max
    # Get only the highest tier per programme
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
    from apps.library.models import Competency
    from apps.sessions.models import Session
    from apps.programmes.models import ProgrammeCompetency

    # Find competencies they already have evidence for
    current_comps = set(
        Evidence.objects.filter(person=person, status="valid")
        .values_list("competency_id", flat=True)
        .distinct()
    )

    # Find target competencies for the programmes they are enrolled in
    programme_ids = tiers.values_list("programme_id", flat=True)
    target_comps = set(
        ProgrammeCompetency.objects.filter(programme_id__in=programme_ids)
        .values_list("competency_id", flat=True)
        .distinct()
    )

    # The gaps are targets they don't have yet
    gaps = target_comps - current_comps

    # Get the next 3 upcoming sessions (regardless of gaps)
    now = timezone.now()
    upcoming_sessions = (
        Session.objects.filter(
            start_at__gte=now,
            status__in=["scheduled", "open"]
        )
        .select_related("programme")
        .order_by("start_at")[:3]
    )

    # Decorate sessions with gap-matching info
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
            "stamp_count": stamps.count(),
            "credentials": credentials,
            "tiers": tiers,
            "recommendations": recommendations,
            "token": token,
        },
    )

def passport_invalid(request):
    """Shown when a passport token is invalid or revoked."""
    return render(request, "public/invalid.html")



# Credential submission and review
@login_required
def credential_submit(request):
    """A signed-in user submits an external certification."""
    try:
        person = request.user.hub_account.person
    except Exception:
        person = None
    if person is None:
        # SRS CRD-02: external participants authenticate via one-time code and
        # may submit certifications; create a Person record on first submission.
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

    if request.method == "POST":
        form = CredentialSubmitForm(request.POST, request.FILES)
        if form.is_valid():
            credential = form.save(commit=False)
            credential.person = person
            credential.save()

            # Auto-approve path (CRD-10)
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
                passport_url = _create_passport_link(credential.person)
                return render(
                    request,
                    "passport/credential_submitted.html",
                    {
                        "auto_approved": True,
                        "credential": credential,
                        "passport_url": passport_url,
                    },
                )

            # Standard review path
            CredentialReview.objects.create(
                credential=credential,
                from_status="",
                to_status="submitted",
                actor=request.user,
                comment="Submitted by user.",
            )
            messages.success(
                request,
                "Credential submitted successfully. It will be reviewed shortly.",
            )
            passport_url = _create_passport_link(credential.person)
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
        form = CredentialSubmitForm()

    return render(
        request,
        "passport/credential_submit.html",
        {"form": form},
    )


@login_required
def credential_review_queue(request):
    """List of credentials pending review, scoped to the reviewer's permissions."""
    from apps.accounts.permissions import get_user_scopes, is_super_admin

    if not _can_review(request.user):
        messages.error(request, "You don't have permission to review credentials.")
        return redirect("dashboard")

    qs = Credential.objects.filter(review_status="submitted").select_related(
        "person"
    )

    # Apply scoping for non-admins
    if not is_super_admin(request.user):
        scopes = get_user_scopes(request.user)

        unit_ids = {
            s.scope_id
            for s in scopes
            if s.role == "focal_person" and s.scope_type == "unit"
        }
        programme_ids = {
            s.scope_id
            for s in scopes
            if s.role == "programme_owner" and s.scope_type == "programme"
        }

        if unit_ids:
            qs = qs.filter(person__unit_id__in=unit_ids)
        elif programme_ids:
            qs = qs.filter(
                catalogue__competencies__competency__programme_targets__programme_id__in=programme_ids
            ).distinct()
        else:
            qs = qs.none()

    qs = qs.order_by("-created_at")

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

    credential = get_object_or_404(Credential, id=credential_id)

    # Compute automated checks (for display)
    from datetime import date
    today = date.today()

    # Check 1: name match
    person_name = (credential.person.full_name or "").lower().strip()
    credential_title = (credential.title or "").lower()
    name_tokens = [t for t in person_name.split() if len(t) > 2]
    name_matches = (
        all(t in credential_title for t in name_tokens) if name_tokens else False
    )

    # Check 2: ID reused by another person?
    id_reused = False
    if credential.credential_id:
        id_reused = (
            Credential.objects.filter(
                credential_id=credential.credential_id
            ).exclude(id=credential.id).exists()
        )

    # Check 3: URL provided?
    has_url = bool(credential.verification_url)

    # Check 4: dates valid?
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

        if decision not in ("approved", "rejected"):
            messages.error(request, "Invalid decision.")
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

            if decision == "approved":
                _generate_evidence_from_credential(credential)

                if credential.catalogue:
                    first_mapping = credential.catalogue.competencies.first()
                    if first_mapping:
                        level_obj = Level.objects.filter(
                            framework__is_knust=True,
                            ordinal=first_mapping.max_level,
                        ).first()
                        if level_obj:
                            badge_jwt = generate_badge_json(
                                credential.person,
                                first_mapping.competency,
                                level_obj,
                            )
                            credential.badge_json = {"jwt": badge_jwt}
                            credential.save()

                send_notification(
                    person=credential.person,
                    template_key="credential_approved",
                    context={
                        "name": credential.person.full_name,
                        "credential_title": credential.title,
                        "passport_url": f"{django_settings.SITE_URL}/",
                    },
                )
                messages.success(
                    request,
                    f"Approved credential for {credential.person.full_name}.",
                )
            else:
                messages.info(
                    request,
                    f"Rejected credential for {credential.person.full_name}.",
                )

        return redirect("credential_review_queue")

    return render(
        request,
        "credentials/review_detail.html",
        {
            "credential": credential,
            "checks": checks,
        },
    )


# Self-assessment (SAS)
@rate_limit("sas_start", max_requests=10, window_seconds=3600)
def sas_start(request):
    """Step 1: Enter KNUST email to start the self-assessment."""
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        if not email.endswith("@knust.edu.gh") and not email.endswith(
            "@st.knust.edu.gh"
        ):
            return render(
                request,
                "public/sas_start.html",
                {"error": "Please use a valid KNUST email address."},
            )

        person, _ = Person.objects.get_or_create(
            email=email,
            defaults={
                "affiliation": "staff",
                "full_name": email.split("@")[0].title(),
            },
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
    pt = PassportToken.objects.filter(
        token_hash=token_hash, revoked_at__isnull=True
    ).first()

    if not pt:
        return render(request, "public/invalid.html")

    person = pt.person


    # PHASE 1: PROFILE CONFIRMATION
    if not person.title or not person.category or (not person.unit_id and not person.unit_other):
        if request.method == "POST":
            print(f"DEBUG POST data: {dict(request.POST)}")
            # Capture the editable full name
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

            return redirect("sas_confirm", token=token)

            units = OrgUnit.objects.filter(
            active=True, 
            type__in=["department", "faculty", "school"]
        ).order_by("name")
        return render(
            request,
            "public/sas_profile.html",
            {"person": person, "token": token, "units": units},
        )


    # PHASE 2: QUESTIONNAIRE
    
    competencies = (
        Competency.objects.filter(status="active")
        .select_related("domain")
        .order_by("domain__display_order", "code")
    )

    if request.method == "POST":
        with transaction.atomic():
            template, _ = SurveyTemplate.objects.get_or_create(
                name="Baseline Self-Assessment",
                defaults={"type": "pre", "definition": {}},
            )
            SelfAssessment.objects.create(
                person=person,
                template=template,
                status="submitted",
                submitted_at=timezone.now(),
            )

            for comp in competencies:
                level_val = request.POST.get(f"comp_{comp.id}")
                if level_val:
                    level = Level.objects.filter(
                        framework__is_knust=True, ordinal=int(level_val)
                    ).first()
                    if level:
                        Evidence.objects.create(
                            person=person,
                            competency=comp,
                            level=level,
                            type="self",
                            source_type="self_assessment",
                            status="valid",
                        )

            # Revoke the SAS token now that the questionnaire is submitted
            pt.revoked_at = timezone.now()
            pt.save()

            # Generate the new passport token
            raw_token = secrets.token_urlsafe(32)
            PassportToken.objects.create(
                person=person,
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            )
            passport_url = f"{settings.SITE_URL}/p/{raw_token}/"

        send_notification(
            person=person,
            template_key="passport_link",
            context={
                "name": person.full_name,
                "session_title": "Self-Assessment",
                "passport_url": passport_url,
            },
        )

        return render(
            request, "public/sas_done.html", {"passport_url": passport_url}
        )

    return render(
        request,
        "public/sas_form.html",
        {"person": person, "competencies": competencies},
    )



# Badge verification
def verify_badge_view(request, badge_jwt):
    """Public page to verify a badge signature."""
    badge_data = verify_badge(badge_jwt)

    if not badge_data:
        return render(request, "public/badge_invalid.html")

    return render(request, "public/badge_valid.html", {"badge": badge_data})



# Resend passport link
@rate_limit("resend", max_requests=3, window_seconds=3600)
def resend_link(request):
    """Public page to request a new passport link."""
    message = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()

        if email:
            person = Person.objects.filter(email__iexact=email).first()
            if person:
                raw_token = secrets.token_urlsafe(32)
                PassportToken.objects.create(
                    person=person,
                    token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                )
                passport_url = f"{settings.SITE_URL}/p/{raw_token}/"

                send_notification(
                    person=person,
                    template_key="passport_link",
                    context={
                        "name": person.full_name,
                        "session_title": "Sua+ Portal",
                        "passport_url": passport_url,
                    },
                )

        message = (
            "If that email is in our system, a new passport link has been sent to it."
        )

    return render(request, "public/resend.html", {"message": message})


# Report an error
@rate_limit("report_error", max_requests=5, window_seconds=3600)
def report_error(request, token):
    """Passport holder reports an error with their record."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(
        token_hash=token_hash, revoked_at__isnull=True
    ).first()

    if not pt:
        return render(request, "public/invalid.html")

    person = pt.person

    if request.method == "POST":
        message = request.POST.get("message", "").strip()
        if message:
            CorrectionRequest.objects.create(person=person, message=message)
            return render(request, "public/error_reported.html")

    return render(
        request,
        "public/report_error.html",
        {"person": person, "token": token},
    )


def passport_transcript(request, token):
    """Printable transcript of the passport."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = (
        PassportToken.objects.filter(token_hash=token_hash)
        .select_related("person")
        .first()
    )
    if not pt or pt.revoked_at:
        raise Http404("Passport not found")

    person = pt.person
    domains = _compute_competency_profile(person)
    credentials = Credential.objects.filter(person=person, status="valid")
    tiers = TierStatus.objects.filter(person=person).select_related("programme")
    stamps = Attendance.objects.filter(
        person=person, status__in=["completed", "partial"]
    ).select_related("session__programme").order_by("-check_in_at")[:50]

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
    
    
def passport_profile(request, token):
    """View a passport owner's profile details."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = PassportToken.objects.filter(
        token_hash=token_hash, revoked_at__isnull=True
    ).select_related("person").first()

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
    pt = PassportToken.objects.filter(
        token_hash=token_hash, revoked_at__isnull=True
    ).select_related("person").first()

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

    # Fetch units for the dropdown
    units = OrgUnit.objects.filter(
        active=True, 
        type__in=["department", "faculty", "school"]
    ).order_by("name")

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
        messages.error(
            request,
            "Your account is not linked to a person record yet. Please contact the administrator.",
        )
        return redirect("/hub/")

    # Generate a fresh, secure token for this session
    raw_token = secrets.token_urlsafe(32)
    PassportToken.objects.create(
        person=person,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
    )

    # Redirect to the passport using the new token
    return redirect("passport", token=raw_token)   