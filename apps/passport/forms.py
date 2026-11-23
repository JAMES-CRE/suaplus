"""Forms for the passport app - credential submission."""

from pathlib import Path
from typing import ClassVar

from django import forms

from apps.library.models import Competency, Level
from apps.passport.models import Credential


class CredentialSubmitForm(forms.ModelForm):
    """Form for a participant to submit an external certification."""

    max_certificate_size = 5 * 1024 * 1024
    allowed_extensions: ClassVar[set] = {".pdf", ".png", ".jpg", ".jpeg"}

    class Meta:
        model = Credential
        fields: ClassVar[list] = [
            "title",
            "issuer",
            "credential_id",
            "issued_at",
            "expires_at",
            "verification_url",
            "file",
            "other_description",
        ]
        widgets: ClassVar[dict] = {
            "issued_at": forms.DateInput(attrs={"type": "date"}),
            "expires_at": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Force the source to be self_submitted and status to submitted
        self.instance.kind = "external"
        self.instance.source = "self_submitted"
        self.instance.review_status = "submitted"
        self.instance.status = "valid"
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-input"
        self.fields["file"].widget.attrs["accept"] = ".pdf,.png,.jpg,.jpeg"
        self.fields["file"].help_text = "PDF, PNG or JPEG, maximum 5 MB."

    def clean(self):
        cleaned = super().clean()
        credential_id = cleaned.get("credential_id", "").strip()
        url = cleaned.get("verification_url", "").strip()
        issued_at = cleaned.get("issued_at")
        expires_at = cleaned.get("expires_at")
        if not credential_id and not url:
            raise forms.ValidationError(
                "Provide a credential ID or a verification URL."
            )
        if issued_at and expires_at and expires_at < issued_at:
            self.add_error("expires_at", "Expiry date cannot be before the issue date.")
        return cleaned

    def clean_file(self):
        certificate = self.cleaned_data.get("file")
        if not certificate:
            raise forms.ValidationError("Upload the certificate file.")
        if certificate.size > self.max_certificate_size:
            raise forms.ValidationError("Certificate files must be 5 MB or smaller.")

        extension = Path(certificate.name).suffix.lower()
        if extension not in self.allowed_extensions:
            raise forms.ValidationError(
                "Upload a PDF, PNG, JPG, or JPEG certificate file."
            )

        header = certificate.read(8)
        certificate.seek(0)
        if extension == ".pdf":
            if not header.startswith(b"%PDF-"):
                raise forms.ValidationError("The uploaded PDF file is not valid.")
            return certificate

        try:
            from PIL import Image

            image = Image.open(certificate)
            image.verify()
        except (OSError, ValueError):
            raise forms.ValidationError("The uploaded image file is not valid.")
        finally:
            certificate.seek(0)
        return certificate


class AssessmentProfileForm(forms.Form):
    full_name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    title = forms.ChoiceField(
        choices=[(v, v) for v in ["Prof.", "Dr.", "Ing.", "Rev.", "Mr.", "Mrs.", "Ms."]]
    )
    category = forms.ChoiceField(
        choices=[
            ("academic", "Academic staff"),
            ("non_academic", "Non-academic staff"),
            ("undergrad", "Undergraduate"),
            ("postgrad", "Postgraduate"),
        ]
    )
    unit = forms.ChoiceField()
    unit_other = forms.CharField(label="Other department", max_length=200, required=False, help_text="Use this only if your department is not listed.")
    phone = forms.CharField(label="Phone number", max_length=30, required=False, widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}), help_text="Use a Ghana local number or include the international country code.")
    age_band = forms.ChoiceField(
        choices=[
            ("unknown", "Prefer not to say"),
            ("under_18", "Under 18"),
            ("18_plus", "18 or over"),
        ]
    )
    consent = forms.BooleanField(
        label="I consent to storing my confirmed profile for this learning record."
    )

    def __init__(self, *args, person, **kwargs):
        from apps.registry.models import OrgUnit

        self.person = person
        super().__init__(*args, **kwargs)
        self.fields["unit"].choices = (
            [("", "Select unit")]
            + [
                (str(pk), name)
                for pk, name in OrgUnit.objects.filter(active=True).values_list(
                    "pk", "name"
                )
            ]
            + [("other", "Other (not listed)")]
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-checkbox" if isinstance(field.widget, forms.CheckboxInput) else "form-input"

    def clean_phone(self):
        from apps.registry.identifiers import normalize_phone

        value = self.cleaned_data["phone"]
        if (
            self.person.age_band == "under_18"
            or self.data.get("age_band") == "under_18"
        ) and value:
            raise forms.ValidationError("Phone numbers must not be stored for minors.")
        return normalize_phone(value) or None

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("unit") == "other" and not cleaned.get("unit_other"):
            self.add_error("unit_other", "Specify your department.")
        if (
            self.person.age_band != "unknown"
            and cleaned.get("age_band") != self.person.age_band
        ):
            self.add_error(
                "age_band", "Ask an administrator to correct an existing age band."
            )
        if (
            cleaned.get("age_band") == "under_18"
            and not self.person.consents.filter(
                type__in=["guardian", "school"], withdrawn_at__isnull=True
            ).exists()
        ):
            self.add_error(
                "age_band", "Guardian or school consent is required before proceeding."
            )
        allowed = {
            "staff": {"academic", "non_academic"},
            "student": {"undergrad", "postgrad"},
        }
        if cleaned.get("category") not in allowed.get(self.person.affiliation, set()):
            self.add_error("category", "Choose a category matching your affiliation.")
        return cleaned


class SelfAssessmentForm(forms.Form):
    consent = forms.BooleanField(
        label="I consent to storing this self-assessment as a learning baseline."
    )

    def __init__(self, *args, competencies, **kwargs):
        self.competencies = list(competencies)
        super().__init__(*args, **kwargs)
        for comp in self.competencies:
            self.fields[f"comp_{comp.pk}"] = forms.TypedChoiceField(
                label=comp.proficient_descriptor or comp.title,
                coerce=int,
                choices=[
                    (level.ordinal, level.label)
                    for level in comp.framework.levels.all()
                ],
                widget=forms.RadioSelect,
            )
        self.questions = [
            {"competency": comp, "field": self[f"comp_{comp.pk}"]}
            for comp in self.competencies
        ]

    def clean(self):
        cleaned = super().clean()
        if not self.competencies:
            raise forms.ValidationError(
                "No assessment questions are currently available."
            )
        unexpected = {key for key in self.data if key.startswith("comp_")} - set(
            self.fields
        )
        if unexpected:
            raise forms.ValidationError(
                "Unexpected assessment question. Reload this page."
            )
        return cleaned


class InternalAssessmentForm(forms.Form):
    competency = forms.ModelChoiceField(queryset=Competency.objects.none())
    level = forms.ModelChoiceField(queryset=Level.objects.none())
    evidence_type = forms.ChoiceField(
        choices=[
            ("task", "Reviewed workshop task"),
            ("assessor", "Assessor evaluation"),
        ]
    )
    note = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Assessment rationale / task reference",
    )

    def __init__(self, *args, attendance, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["competency"].empty_label = "Choose a workshop competency"
        self.fields["level"].empty_label = "Choose the assessed level"
        self.fields["competency"].queryset = Competency.objects.filter(
            pk__in=attendance.session.competencies.values("competency_id"),
            status__in=["active", "published"],
            framework__is_knust=True,
        )
        self.fields["level"].queryset = Level.objects.filter(
            framework_id__in=self.fields["competency"].queryset.values("framework_id")
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-checkbox" if isinstance(field.widget, forms.CheckboxInput) else "form-input"
