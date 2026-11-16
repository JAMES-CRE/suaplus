"""Forms for the passport app - credential submission."""

from pathlib import Path

from django import forms

from apps.passport.models import Credential


class CredentialSubmitForm(forms.ModelForm):
    """Form for a participant to submit an external certification."""

    max_certificate_size = 5 * 1024 * 1024
    allowed_extensions = {".pdf", ".png", ".jpg", ".jpeg"}

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
            raise forms.ValidationError("Upload a PDF, PNG, JPG, or JPEG certificate file.")

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
