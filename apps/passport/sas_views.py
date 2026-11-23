"""Single-purpose self-assessment HTTP flows."""

import hashlib

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.core.rate_limit import rate_limit
from apps.notifications.services import send_notification
from apps.passport.assessment import (
    assessment_competencies,
    issue_assessment_link,
    submit_assessment,
)
from apps.passport.forms import AssessmentProfileForm, SelfAssessmentForm
from apps.passport.linking import issue_passport_link
from apps.passport.models import AssessmentLink
from apps.registry.identity import identity_data, resolve_or_create
from apps.registry.models import Consent, Person


def send_self_assessment_link(person):
    raw = issue_assessment_link(person)
    url = f"{settings.SITE_URL}/sas/confirm/{raw}/"
    send_mail(
        "Your Sua+ Self-Assessment Link",
        f"Start your assessment (valid for 24 hours): {url}",
        settings.DEFAULT_FROM_EMAIL,
        [person.email],
    )


@rate_limit("sas_start", max_requests=10, window_seconds=3600)
def sas_start(request):
    if request.method == "POST":
        try:
            submitted = identity_data(email=request.POST.get("email", ""))
            if not submitted["email"].endswith(("@knust.edu.gh", "@st.knust.edu.gh")):
                raise ValidationError("Use a valid KNUST email address.")
            result = resolve_or_create(
                submitted,
                source="sas",
                default_name=submitted["email"].split("@")[0].title(),
            )
            if result.status in {"new", "matched"}:
                send_self_assessment_link(result.person)
        except ValidationError:
            return render(
                request,
                "public/sas_start.html",
                {"error": "Use a valid KNUST email address."},
            )
        return render(request, "public/sas_email_sent.html")
    return render(request, "public/sas_start.html")


def sas_confirm(request, token):
    link = (
        AssessmentLink.objects.filter(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            consumed_at__isnull=True,
            expires_at__gt=timezone.now(),
            person__is_anonymised=False,
            person__merged_into__isnull=True,
        )
        .select_related("person")
        .first()
    )
    if link is None:
        return render(request, "public/invalid.html", status=400)
    person = link.person
    if not link.profile_confirmed_at:
        initial = {
            "full_name": person.full_name,
            "title": person.title,
            "category": person.category,
            "unit": str(person.unit_id) if person.unit_id else "other",
            "unit_other": person.unit_other,
            "phone": person.phone,
            "age_band": person.age_band,
        }
        form = AssessmentProfileForm(
            request.POST if request.method == "POST" else None,
            person=person,
            initial=initial,
        )
        if request.method == "POST" and form.is_valid():
            with transaction.atomic():
                person = Person.objects.select_for_update().get(
                    pk=person.pk, is_anonymised=False, merged_into__isnull=True
                )
                locked = AssessmentLink.objects.select_for_update().get(pk=link.pk)
                if locked.consumed_at or locked.expires_at <= timezone.now():
                    return render(request, "public/invalid.html", status=400)
                for key in ["full_name", "title", "category", "phone", "age_band"]:
                    setattr(person, key, form.cleaned_data[key])
                person.unit_id = (
                    None
                    if form.cleaned_data["unit"] == "other"
                    else int(form.cleaned_data["unit"])
                )
                person.unit_other = (
                    form.cleaned_data["unit_other"] if person.unit_id is None else ""
                )
                try:
                    person.full_clean()
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    person.save()
                    Consent.objects.create(
                        person=person,
                        type="data_processing",
                        given_by=person.full_name,
                        notice_version="1.0",
                    )
                    locked.profile_confirmed_at = timezone.now()
                    locked.save(update_fields=["profile_confirmed_at"])
                    return redirect("sas_confirm", token=token)
        return render(
            request, "public/sas_profile.html", {"person": person, "form": form}
        )
    form = SelfAssessmentForm(
        request.POST if request.method == "POST" else None,
        competencies=assessment_competencies(),
    )
    revision = ":".join(f"{comp.pk}:{comp.framework_id}:{comp.framework.revision}:" + ";".join(f"{level.pk}-{level.ordinal}-{level.label}" for level in comp.framework.levels.all()) for comp in form.competencies)
    draft_key = hashlib.sha256(f"{token}:{revision}".encode()).hexdigest()
    frameworks = list({comp.framework_id: comp.framework for comp in form.competencies}.values())
    if request.method == "POST" and form.is_valid():
        try:
            submit_assessment(link.pk, form)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            url = issue_passport_link(person)
            send_notification(
                person=person,
                template_key="passport_link",
                context={
                    "name": person.full_name,
                    "session_title": "Self-Assessment",
                    "passport_url": url,
                },
            )
            return render(request, "public/sas_done.html", {"passport_url": url, "assessment_draft_key": draft_key})
    return render(
        request,
        "public/sas_form.html",
        {"person": person, "competencies": form.competencies, "form": form, "assessment_draft_key": draft_key, "assessment_frameworks": frameworks},
    )
