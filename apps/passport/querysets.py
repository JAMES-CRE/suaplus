"""Evidence validity at read time; scheduled expiry is not an access boundary."""

from django.db.models import F, Q
from django.utils import timezone

from apps.passport.models import Evidence


def valid_evidence(queryset=None):
    qs = Evidence.objects.all() if queryset is None else queryset
    today = timezone.localdate()
    return (
        qs.filter(
            status="valid",
            is_expired=False,
            person__is_anonymised=False,
            person__merged_into__isnull=True,
            level__framework_id=F("competency__framework_id"),
        )
        .filter(
            Q(internalassessment__isnull=True)
            | (
                Q(
                    internalassessment__credential__status="valid",
                    internalassessment__credential__review_status="approved",
                )
                & (
                    Q(internalassessment__credential__expires_at__isnull=True)
                    | Q(internalassessment__credential__expires_at__gte=today)
                )
            )
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=today))
        .filter(
            Q(credential__isnull=True)
            | (
                Q(credential__status="valid", credential__review_status="approved")
                & (
                    Q(credential__expires_at__isnull=True)
                    | Q(credential__expires_at__gte=today)
                )
            )
        )
    )
