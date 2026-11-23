"""Validated, atomic SAS submission using dedicated expiring links."""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.library.models import Competency, SurveyTemplate
from apps.library.versioning import framework_version_label
from apps.passport.models import AssessmentLink, Evidence, SelfAssessment
from apps.registry.models import Consent, Person


def assessment_competencies():
    return (
        Competency.objects.filter(
            status__in=["active", "published"],
            framework__is_knust=True,
            framework__status__in=["active", "published"],
            domain__status__in=["active", "published"],
        )
        .select_related("framework", "domain")
        .prefetch_related("framework__levels")
        .order_by("domain_id", "code")
    )


def issue_assessment_link(person):
    raw = secrets.token_urlsafe(32)
    with transaction.atomic():
        Person.objects.select_for_update().get(
            pk=person.pk, is_anonymised=False, merged_into__isnull=True
        )
        AssessmentLink.objects.filter(person=person, consumed_at__isnull=True).update(
            consumed_at=timezone.now()
        )
        AssessmentLink.objects.create(
            person=person,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=timezone.now()
            + timedelta(hours=getattr(settings, "SAS_LINK_HOURS", 24)),
        )
    return raw


def submit_assessment(link_id, form):
    with transaction.atomic():
        person_id = AssessmentLink.objects.values_list("person_id", flat=True).get(
            pk=link_id
        )
        person = Person.objects.select_for_update().get(
            pk=person_id, is_anonymised=False, merged_into__isnull=True
        )
        link = AssessmentLink.objects.select_for_update().get(pk=link_id)
        if (
            link.consumed_at
            or link.expires_at <= timezone.now()
            or not link.profile_confirmed_at
        ):
            raise ValidationError(
                "This assessment link is expired, used, or not profile-confirmed."
            )
        if not form.is_valid():
            raise ValidationError(
                "Complete every assessment question and consent field."
            )
        current = set(assessment_competencies().values_list("pk", flat=True))
        if current != {comp.pk for comp in form.competencies}:
            raise ValidationError(
                "The questionnaire changed. Reload and answer the current questions."
            )
        if (
            person.age_band == "under_18"
            and not person.consents.filter(
                type__in=["guardian", "school"], withdrawn_at__isnull=True
            ).exists()
        ):
            raise ValidationError(
                "Guardian or school consent is required for this participant."
            )
        template = SurveyTemplate.objects.filter(
            type="pre", is_default=True, status__in=["active", "published"]
        ).first()
        if template is None:
            template, _ = SurveyTemplate.objects.get_or_create(
                name="Baseline Self-Assessment",
                defaults={"type": "pre", "definition": {}},
            )
        answers = {
            str(comp.pk): form.cleaned_data[f"comp_{comp.pk}"]
            for comp in form.competencies
        }
        assessment = SelfAssessment.objects.create(
            person=person,
            template=template,
            answers=answers,
            status="submitted",
            submitted_at=timezone.now(),
            needs_focal_check=bool(person.unit_other),
        )
        for comp in form.competencies:
            level = comp.framework.levels.get(ordinal=answers[str(comp.pk)])
            Evidence.objects.create(
                person=person,
                competency=comp,
                level=level,
                type="self",
                source_type="self_assessment",
                source_id=assessment.pk,
                framework_version=framework_version_label(comp.framework),
            )
        Consent.objects.create(
            person=person,
            type="data_processing",
            given_by=person.full_name,
            notice_version="1.0",
        )
        link.consumed_at = timezone.now()
        link.save(update_fields=["consumed_at"])
        return assessment
