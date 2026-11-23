"""Scoped identity review; incoming data never expands personal-data scopes."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import require_capability
from apps.registry.models import IdentityConflict
from apps.registry.workflows import (
    conflict_queryset,
    resolve_conflict,
    visible_candidates,
)
from apps.sessions.services import send_passport_update


class IdentityResolutionForm(forms.Form):
    action = forms.ChoiceField(choices=[("match", "Use an existing person"), ("create", "Confirm a distinct new person"), ("reject", "Reject this submission")])
    person = forms.ModelChoiceField(queryset=None, required=False)
    organisation = forms.ModelChoiceField(queryset=None, required=False)
    note = forms.CharField(max_length=2000, widget=forms.Textarea, label="Reason for the decision")

    def __init__(self, *args, conflict, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["person"].queryset = visible_candidates(conflict, user)
        self.fields["organisation"].queryset = conflict.organisation_candidates.all()
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-input"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action") == "match" and not cleaned.get("person"):
            self.add_error("person", "Select a visible candidate identity.")
        if cleaned.get("action") == "create" and self.fields["organisation"].queryset.exists() and not cleaned.get("organisation"):
            self.add_error("organisation", "Select the correct organisation.")
        return cleaned


@login_required
@require_capability("edit_records")
def identity_review_list(request):
    status = request.GET.get("status", "pending")
    if status not in dict(IdentityConflict.STATUS_CHOICES):
        status = "pending"
    rows = conflict_queryset(request.user).filter(status=status).select_related("session")
    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    return render(request, "registry/identity_review_list.html", {"conflicts": page, "status": status, "statuses": IdentityConflict.STATUS_CHOICES})


@login_required
@require_capability("edit_records")
def identity_review_detail(request, conflict_id):
    conflict = get_object_or_404(conflict_queryset(request.user).select_related("session"), pk=conflict_id)
    candidates = visible_candidates(conflict, request.user)
    hidden_candidates = conflict.candidates.exclude(pk__in=candidates.values("pk")).exists()
    form = IdentityResolutionForm(request.POST or None, conflict=conflict, user=request.user)
    if request.method == "POST" and form.is_valid():
        values = form.cleaned_data
        try:
            conflict, attendance, created = resolve_conflict(
                conflict.pk, request.user, action=values["action"],
                person_id=values["person"].pk if values.get("person") else None,
                organisation_id=values["organisation"].pk if values.get("organisation") else None,
                note=values["note"], request=request,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            if conflict.selected_person_id and conflict.source == "sas":
                from apps.passport.views import send_self_assessment_link
                send_self_assessment_link(conflict.selected_person)
            elif conflict.selected_person_id and conflict.source == "resend":
                send_passport_update(conflict.selected_person, "Sua+ Portal")
            if attendance and created and conflict.source == "import":
                send_passport_update(attendance.person, attendance.session.title)
            messages.success(request, "Identity submission resolved.")
            return redirect("identity_review_detail", conflict_id=conflict.pk)
    return render(request, "registry/identity_review_detail.html", {
        "conflict": conflict, "identity": conflict.payload["identity"], "candidates": candidates,
        "hidden_candidates": hidden_candidates, "form": form,
    })
