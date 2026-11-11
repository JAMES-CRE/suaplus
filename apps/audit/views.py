"""Governance views - Data Subject Requests and Audit Logs."""

import json
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import log_event
from apps.registry.models import Person


@staff_member_required
def dsr_dashboard(request):
    """List people to manage DSRs, with search."""
    q = request.GET.get("q", "").strip()
    people = Person.objects.all().order_by("full_name")

    if q:
        from django.db.models import Q
        people = people.filter(
            Q(full_name__icontains=q)
            | Q(email__icontains=q)
            | Q(staff_or_student_no__icontains=q)
        )

    # Recent DSR actions from the audit log
    from apps.audit.models import AuditEvent
    recent_requests = (
        AuditEvent.objects.filter(
            action__in=["dsr_export", "dsr_erase"]
        )
        .select_related("actor")
        .order_by("-at")[:5]
    )

    return render(
        request,
        "audit/dsr_dashboard.html",
        {
            "people": people,
            "q": q,
            "recent_requests": recent_requests,
        },
    )


@staff_member_required
def dsr_export(request, person_id):
    """Export all data for a person as a JSON file."""
    person = get_object_or_404(Person, id=person_id)
    
    # Gather data
    data = {
        "person": {
            "id": person.id,
            "full_name": person.full_name,
            "email": person.email,
            "phone": person.phone,
            "affiliation": person.affiliation,
            "category": person.category,
        },
        "consents": list(person.consents.values("type", "given_at", "notice_version")),
        "attendances": list(person.attendances.values("session__title", "status", "check_in_at")),
        "evidence": list(person.evidence.values("competency__code", "level__ordinal", "type", "assessed_at")),
        "credentials": list(person.credentials.values("title", "issuer", "status")),
    }

    log_event(
        request=request,
        action="dsr_export",
        entity="Person",
        entity_id=person.id,
        after={"exported": True}
    )

    response = HttpResponse(
        json.dumps(data, indent=2, cls=DjangoJSONEncoder),
        content_type="application/json"
    )
    response["Content-Disposition"] = f'attachment; filename="dsr_{person.id}.json"'
    return response


@staff_member_required
def dsr_erase(request, person_id):
    """Anonymise a person's data to satisfy the Right to Erasure."""
    person = get_object_or_404(Person, id=person_id)

    if request.method == "POST":
        if not person.is_anonymised:
            person.full_name = f"Anonymised User {person.id}"
            person.email = f"anon_{person.id}@example.com"
            person.phone = None
            person.staff_or_student_no = None
            person.is_anonymised = True
            person.anonymised_at = timezone.now()
            person.save()

            log_event(
                request=request,
                action="dsr_erase",
                entity="Person",
                entity_id=person.id,
                before={"email": "original"},
                after={"anonymised": True}
            )
            messages.success(request, f"Anonymised {person.full_name}.")
        else:
            messages.info(request, "This person is already anonymised.")

        return redirect("dsr_dashboard")

    return render(request, "audit/dsr_erase.html", {"person": person})


@staff_member_required
def audit_log_list(request):
    """Read-only list of all audit events."""
    events = AuditEvent.objects.select_related("actor").order_by("-at")[:500]
    return render(request, "audit/audit_log.html", {"events": events})