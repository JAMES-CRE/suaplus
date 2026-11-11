"""Check-in form validates participant identity, consent, optional pre-ratings."""

from django import forms

from apps.registry.models import Person


class CheckinForm(forms.Form):
    AFFILIATION_CHOICES = [
        ("staff", "Staff"),
        ("student", "Student"),
        ("external", "External"),
    ]

    AGE_BAND_CHOICES = [
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
        required=True,
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

        if aff in ("staff", "student") and not (number or email):
            raise forms.ValidationError(
                "Staff and students must provide either a staff/student number or an email."
            )

        if aff == "external" and not (email or phone):
            raise forms.ValidationError(
                "External participants must provide either an email or a phone number."
            )

        if age_band == "under_18":
            if not guardian_name or not guardian_contact:
                raise forms.ValidationError(
                    "For participants under 18, guardian name and contact are required."
                )
            if phone:
                raise forms.ValidationError(
                    "Phone numbers are not stored for minors. Please remove the phone field."
                )

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
    """Form for creating or editing a session."""

    start_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Start date",
    )
    start_time = forms.TimeField(
        widget=forms.TimeInput(attrs={"type": "time"}),
        label="Start time",
    )
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="End date",
    )
    end_time = forms.TimeField(
        widget=forms.TimeInput(attrs={"type": "time"}),
        label="End time",
    )

    class Meta:
        from .models import Session

        model = Session
        fields = [
            "programme",
            "title",
            "mode",
            "venue",
            "online_link",
            "host_unit",
            "region",
            "district",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # If editing an existing session, prefill date/time fields
        if self.instance and self.instance.pk:
            if self.instance.start_at:
                self.fields["start_date"].initial = self.instance.start_at.date()
                self.fields["start_time"].initial = self.instance.start_at.time()
            if self.instance.end_at:
                self.fields["end_date"].initial = self.instance.end_at.date()
                self.fields["end_time"].initial = self.instance.end_at.time()
        else:
            # Default for new sessions: today, 10:00 to 13:00
            from django.utils import timezone
            today = timezone.now().date()
            self.fields["start_date"].initial = today
            self.fields["end_date"].initial = today
            import datetime
            self.fields["start_time"].initial = datetime.time(10, 0)
            self.fields["end_time"].initial = datetime.time(13, 0)

        # Apply Tailwind class to every field
        for field_name, field in self.fields.items():
            field.widget.attrs["class"] = "form-input"

    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get("start_date")
        start_time = cleaned.get("start_time")
        end_date = cleaned.get("end_date")
        end_time = cleaned.get("end_time")

        if start_date and start_time and end_date and end_time:
            import datetime
            start = datetime.datetime.combine(start_date, start_time)
            end = datetime.datetime.combine(end_date, end_time)

            if end <= start:
                raise forms.ValidationError("End time must be after start time.")

        return cleaned