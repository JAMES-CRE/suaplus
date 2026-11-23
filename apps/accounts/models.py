# Create your models here.
"""Accounts models for Hub users and their scoped roles."""
from typing import ClassVar

from django.conf import settings
from django.db import models

from apps.registry.models import Person


class UserAccount(models.Model):
    """A Hub user. Only a few hundred people have one."""

    AUTH_METHOD_CHOICES: ClassVar[list] = [
        ("google", "Google sign-in"),
        ("otp", "Email one-time code"),
    ]

    person = models.OneToOneField(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_account",
        help_text="The person this account represents",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_account",
        help_text="Django auth user",
    )
    auth_method = models.CharField(
        max_length=10, choices=AUTH_METHOD_CHOICES, default="google"
    )
    mfa_enabled = models.BooleanField(default=False)
    idle_timeout_minutes = models.PositiveSmallIntegerField(default=30)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Hub account"
        verbose_name_plural = "Hub accounts"

    def __str__(self):
        email = self.user.email if self.user_id else "(no user)"
        return f"{email} ({self.get_auth_method_display()})"


class RoleAssignment(models.Model):
    """Which role a Hub user holds, and at what scope."""

    ROLE_CHOICES: ClassVar[list] = [
        ("participant", "Participant"),
        ("associate_trainer", "Associate Trainer"),
        ("facilitator", "Facilitator"),
        ("partner_coordinator", "Partner Coordinator"),
        ("focal_person", "College Focal Person"),
        ("programme_owner", "Programme Owner"),
        ("executive_viewer", "Executive Viewer"),
        ("super_admin", "Super Admin"),
        ("library_curator", "Library Curator"),
    ]

    SCOPE_TYPE_CHOICES: ClassVar[list] = [
        ("own", "Own record only"),
        ("session", "Session"),
        ("programme", "Programme"),
        ("unit", "Organisation unit"),
        ("organisation", "External organisation"),
        ("all", "All data"),
    ]

    user_account = models.ForeignKey(
        UserAccount,
        on_delete=models.CASCADE,
        related_name="roles",
    )
    role = models.CharField(max_length=30, choices=ROLE_CHOICES)
    scope_type = models.CharField(max_length=15, choices=SCOPE_TYPE_CHOICES)
    scope_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="ID in the scoped table (programme, unit, organisation, session)",
    )
    granted_by = models.ForeignKey(
        UserAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="roles_granted",
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list] = ["-granted_at"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["user_account", "role", "scope_type", "scope_id"],
                name="uniq_role_scope",
            ),
        ]
        indexes: ClassVar[list] = [
            models.Index(fields=["user_account", "role"]),
        ]

    def clean(self):
        from django.apps import apps
        from django.core.exceptions import ValidationError

        from apps.accounts.permissions import ROLE_SCOPES

        super().clean()
        if self.scope_type not in ROLE_SCOPES.get(self.role, set()):
            raise ValidationError({"scope_type": "This scope is not permitted for the selected role."})
        if self.scope_type in {"own", "all"}:
            if self.scope_id is not None:
                raise ValidationError({"scope_id": "Own/all scopes must not have an object ID."})
        else:
            targets = {"unit": "registry.OrgUnit", "programme": "programmes.Programme", "session": "training.Session", "organisation": "registry.Organisation"}
            model = apps.get_model(targets[self.scope_type])
            if self.scope_id is None or not model.objects.filter(pk=self.scope_id).exists():
                raise ValidationError({"scope_id": "Choose an existing object for this scope."})

    def __str__(self):
        scope = f"{self.scope_type}:{self.scope_id}" if self.scope_id else self.scope_type
        return f"{self.user_account} - {self.get_role_display()} ({scope})"