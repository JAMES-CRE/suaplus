"""Forms for the passport app - credential submission."""

from django import forms

from apps.passport.models import Credential


class CredentialSubmitForm(forms.ModelForm):
    """Form for a participant to submit an external certification."""

    class Meta:
        model = Credential
        fields = [
            "title",
            "issuer",
            "credential_id",
            "issued_at",
            "expires_at",
            "verification_url",
            "file",
            "other_description",
        ]
        widgets = {
            "issued_at": forms.DateInput(attrs={"type": "date"}),
            "expires_at": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Force the source to be self_submitted and status to submitted
        self.instance.source = "self_submitted"
        self.instance.review_status = "submitted"
        self.instance.status = "valid"

    def clean(self):
        cleaned = super().clean()
        # Require either a verification URL or a file upload for proof
        url = cleaned.get("verification_url")
        file = cleaned.get("file")
        if not url and not file:
            raise forms.ValidationError(
                "Please provide either a verification URL or upload the certificate file."
            )
        return cleaned