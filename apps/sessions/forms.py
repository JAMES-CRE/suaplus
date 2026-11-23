"""Check-in form validates participant identity, consent, optional pre-ratings."""
from typing import ClassVar

from django import forms


class CheckinForm(forms.Form):
    AFFILIATION_CHOICES: ClassVar[list] = [
        ("staff", "Staff"),
        ("student", "Student"),
        ("external", "External"),
    ]

    AGE_BAND_CHOICES: ClassVar[list] = [
        ("18_plus", "18 or over"),
        ("under_18", "Under 18"),
    ]

    affiliation = forms.ChoiceField(choices=AFFILIATION_CHOICES)
    full_name = forms.CharField(max_length=200)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=20, required=False)
    staff_or_student_no = forms.CharField(max_length=30, required=False)
    organisation_name = forms.CharField(max_length=250, required=False)

    consent = forms.BooleanField(
        required=False,
        label="I consent to Sua+ recording my data for training records",
    )
    sms_consent = forms.BooleanField(
        required=False, label="Send me SMS updates"
    )

    age_band = forms.ChoiceField(
        choices=AGE_BAND_CHOICES,
        widget=forms.RadioSelect,
        label="Are you 18 or over?",
        initial="18_plus",
    )
    guardian_name = forms.CharField(max_length=200, required=False)
    guardian_contact = forms.CharField(max_length=100, required=False)
    guardian_consent = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Apply Tailwind form-input class to every text/email/select field
        for field_name, field in self.fields.items():
            if field_name not in ("consent", "sms_consent", "age_band"):
                field.widget.attrs["class"] = "form-input"

    def clean(self):
        cleaned = super().clean()
        aff = cleaned.get("affiliation")
        email = cleaned.get("email", "").strip()
        phone = cleaned.get("phone", "").strip()
        number = cleaned.get("staff_or_student_no", "").strip()
        age_band = cleaned.get("age_band")
        guardian_name = cleaned.get("guardian_name", "").strip()
        guardian_contact = cleaned.get("guardian_contact", "").strip()
        guardian_consent = cleaned.get("guardian_consent", False)
        consent = cleaned.get("consent", False)

        if aff in ("staff", "student") and not (number or email):
            raise forms.ValidationError(
                "Staff and students must provide either a staff/student number or an email."
            )

        if aff == "external" and not (email or phone or cleaned.get("organisation_name", "").strip()):
            raise forms.ValidationError(
                "External participants must provide an email, phone, or organisation with their full name."
            )
        from apps.registry.identifiers import (
            normalize_email,
            normalize_number,
            normalize_phone,
        )
        cleaned["email"] = normalize_email(email)
        cleaned["staff_or_student_no"] = normalize_number(number)
        try:
            cleaned["phone"] = normalize_phone(phone)
        except forms.ValidationError as exc:
            self.add_error("phone", exc)


        if age_band == "under_18":
            if not guardian_name or not guardian_contact or not guardian_consent:
                raise forms.ValidationError(
                    "For participants under 18, guardian name, contact, and consent are required."
                )
            if phone:
                raise forms.ValidationError(
                    "Phone numbers are not stored for minors. Please remove the phone field."
                )
        elif not consent:
            raise forms.ValidationError("Consent is required to check in.")

        return cleaned


class CheckoutForm(forms.Form):
    csat = forms.IntegerField(min_value=1, max_value=5)
    what_will_you_apply = forms.CharField(
        widget=forms.Textarea, required=False, max_length=1000
    )
    interested_in_training = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name != "interested_in_training":
                field.widget.attrs["class"] = "form-input"


class SessionForm(forms.ModelForm):
    """Scoped programme mappings with aware dates and frozen training history."""

    from apps.library.models import Competency

    competencies = forms.ModelMultipleChoiceField(
        queryset=Competency.objects.none(), widget=forms.CheckboxSelectMultiple,
        label="Competencies covered", help_text="Select the skills this workshop actually teaches.",
    )
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))

    class Meta:
        from .models import Session

        model = Session
        fields: ClassVar[list] = [
            "programme", "title", "mode", "venue", "online_link", "host_unit", "region", "district",
        ]

    def __init__(self, *args, programmes=None, **kwargs):
        import datetime

        from django.db.models import Q
        from django.utils import timezone

        from apps.library.models import Competency
        from apps.programmes.models import Programme

        super().__init__(*args, **kwargs)
        self.fields["programme"].queryset = programmes if programmes is not None else Programme.objects.none()
        self.mappings_locked = bool(self.instance.pk and self.instance.attendances.exists())
        if self.instance.pk:
            selected = self.instance.programme
            self.fields["programme"].disabled = True
            for name, value in (("start", self.instance.start_at), ("end", self.instance.end_at)):
                local = timezone.localtime(value)
                self.initial[f"{name}_date"] = local.date()
                self.initial[f"{name}_time"] = local.time()
        else:
            identifier = self.data.get("programme") if self.is_bound else self.initial.get("programme")
            choices = self.fields["programme"].queryset
            selected = choices.filter(pk=identifier).first() if identifier and str(identifier).isdecimal() else None
            if not self.is_bound and selected is None:
                selected = choices.order_by("pk").first()
            if selected and not self.is_bound:
                self.initial["programme"] = selected.pk
            for name in ("start", "end"):
                self.initial.setdefault(f"{name}_date", timezone.localdate())
                self.initial.setdefault(f"{name}_time", datetime.time(10 if name == "start" else 13))
        allowed = Competency.objects.none()
        if selected:
            allowed = Competency.objects.filter(
                programme_targets__programme=selected, framework__is_knust=True,
                framework__status__in=["active", "published"], domain__status__in=["active", "published"],
                status__in=["active", "published"],
            )
        existing = list(self.instance.competencies.values_list("competency_id", flat=True)) if self.instance.pk else []
        if self.mappings_locked:
            allowed = Competency.objects.filter(Q(pk__in=allowed.values("pk")) | Q(pk__in=existing))
            self.fields["competencies"].disabled = True
            self.fields["competencies"].required = bool(existing)
            self.fields["competencies"].help_text = "Mappings are locked after the first check-in to preserve evidence history."
        self.fields["competencies"].queryset = allowed.distinct().order_by("domain__display_order", "code")
        self.initial["competencies"] = existing
        for name, field in self.fields.items():
            if name != "competencies":
                field.widget.attrs["class"] = "form-input"

    def clean(self):
        import datetime

        from django.utils import timezone

        cleaned = super().clean()
        values = [cleaned.get(key) for key in ("start_date", "start_time", "end_date", "end_time")]
        if all(value is not None for value in values):
            start = timezone.make_aware(datetime.datetime.combine(values[0], values[1]))
            end = timezone.make_aware(datetime.datetime.combine(values[2], values[3]))
            if end <= start:
                raise forms.ValidationError("End time must be after start time.")
            self.instance.start_at, self.instance.end_at = start, end
        return cleaned

    def save_mappings(self):
        from apps.sessions.services import set_session_competencies
        set_session_competencies(self.instance, self.cleaned_data["competencies"])
