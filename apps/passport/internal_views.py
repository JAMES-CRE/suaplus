from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import require_capability, scope_records
from apps.passport.awards import issue_internal_award
from apps.passport.forms import InternalAssessmentForm
from apps.sessions.models import Attendance
from apps.sessions.services import send_passport_update


@login_required
@require_capability("record_credentials")
@require_capability("edit_records")
def internal_assessment(request, attendance_id):
    attendance = get_object_or_404(
        scope_records(
            Attendance.objects.all(), request.user, "edit_records"
        ).select_related("session", "person"),
        pk=attendance_id,
    )
    form = InternalAssessmentForm(
        request.POST if request.method == "POST" else None, attendance=attendance
    )
    if request.method == "POST" and form.is_valid():
        try:
            _award, created = issue_internal_award(
                attendance.pk, request.user, request=request, **form.cleaned_data
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            if created:
                send_passport_update(attendance.person, attendance.session.title)
            messages.success(
                request,
                "Internal credential recorded."
                if created
                else "This assessment was already recorded.",
            )
            return redirect("session_console", session_id=attendance.session_id)
    from apps.passport.awards import evidence_cap
    rules = []
    for competency in form.fields["competency"].queryset:
        for kind, label in form.fields["evidence_type"].choices:
            try:
                cap = evidence_cap(competency, kind)
            except ValidationError:
                cap = None
            rules.append({"competency": competency, "kind": label, "cap": cap})
    return render(request, "credentials/internal_assessment.html", {"attendance": attendance, "form": form, "assessment_rules": rules})


def badge_public_key(request):
    from django.conf import settings
    from django.http import HttpResponse

    key = getattr(settings, "BADGE_PUBLIC_KEY", "")
    return HttpResponse(
        key or "Badge signing not configured",
        content_type="text/plain",
        status=200 if key else 503,
    )
