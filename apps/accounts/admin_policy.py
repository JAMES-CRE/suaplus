"""Apply SRS policy to Django admin, including inlines and autocompletes.

Django is_staff/model permissions alone never grant Sua+ data access. Existing
custom Library publication/read-only checks remain in force.
"""
from types import MethodType

from django.contrib import admin
from django.contrib.admin.options import InlineModelAdmin
from django.core.exceptions import PermissionDenied

from apps.accounts.permissions import (
    can,
    can_import_person,
    is_super_admin,
    person_id_for,
    person_queryset,
    programmes_for_creation,
    scope_records,
)

# (read, change, add). Unknown/third-party models are super-admin-only.
RULES = {
    "registry.person": ("view_people", "edit_people", "edit_people"),
    "registry.organisation": ("view_people", "manage_units", "manage_units"),
    "registry.orgunit": ("manage_units",) * 3,
    "registry.unitheadcount": ("manage_units",) * 3,
    "programmes.programme": ("manage_programmes", "manage_programmes", "manage_users"),
    "programmes.programmeowner": ("manage_users",) * 3,
    "programmes.partner": ("manage_programmes", "manage_users", "manage_users"),
    "programmes.programmecompetency": ("manage_programmes",) * 3,
    "programmes.programmepartner": ("manage_programmes",) * 3,
    "programmes.tiercriterion": ("manage_programmes",) * 3,
    "programmes.tierstatus": ("nominate_trainers", None, None),
    "programmes.nomination": ("nominate_trainers", None, None),
    "training.session": ("view_sessions", "manage_sessions", "create_sessions"),
    "training.sessioncompetency": ("manage_sessions",) * 3,
    "training.sessionpartner": ("manage_sessions",) * 3,
    "training.facilitation": ("view_sessions", "manage_sessions", "manage_sessions"),
    "training.attendance": ("view_people", "edit_records", "import_attendance"),
    "training.surveyresponse": ("view_people", "edit_records", "edit_records"),
    "training.importbatch": ("import_attendance", None, None),
    "training.importmappingtemplate": ("import_attendance",) * 3,
    "passport.evidence": ("view_people", "edit_records", "edit_records"),
    "passport.credential": ("view_people", "review_credentials", "record_credentials"),
    "passport.credentialreview": ("review_credentials", None, None),
    "passport.selfassessment": ("review_credentials", None, None),
    "passport.correctionrequest": ("view_people", "edit_records", None),
    "reporting.memo": ("export_reports", None, None),
    "reporting.target": ("manage_targets",) * 3,
    "reporting.exportlog": ("audit", None, None),
    "audit.auditevent": ("audit", None, None),
    "core.academicperiod": ("manage_calendar",) * 3,
    "notifications.notificationtemplate": ("manage_notifications",) * 3,
    "notifications.notification": ("manage_notifications", None, None),
}
REFERENCE_MODELS = {
    "framework", "domain", "competency", "level", "crosswalk", "evidencerule",
    "certificationcatalogue", "certificationcompetency", "surveytemplate",
}
IMMUTABLE = {
    "programmes.tierstatus", "programmes.nomination", "training.importbatch",
    "passport.credentialreview", "passport.selfassessment", "reporting.memo",
    "audit.auditevent", "reporting.exportlog", "library.libraryversion",
}


def _capability(model, action):
    if model._meta.app_label == "library":
        return "manage_library"
    return RULES.get(model._meta.label_lower, ("manage_users",) * 3)[{"view": 0, "change": 1, "add": 2}[action]]


def _reference_reader(user, model):
    return model._meta.app_label == "library" and model._meta.model_name in REFERENCE_MODELS and any(
        can(user, capability) for capability in ("manage_library", "manage_programmes", "create_sessions", "edit_records")
    )


def _read_queryset(user, model):
    qs = model.objects.all()
    if is_super_admin(user):
        return qs
    if _reference_reader(user, model):
        if not can(user, "manage_library") and any(f.name == "status" for f in model._meta.fields):
            qs = qs.exclude(status="draft")
        return qs
    if model._meta.label_lower == "passport.credentialreview":
        from apps.passport.models import Credential
        return qs.filter(credential_id__in=scope_records(Credential.objects.all(), user, "review_credentials").values("pk"))
    return scope_records(qs, user, _capability(model, "view"))


def _candidate_allowed(user, obj, capability):
    """Validate submitted relationships, not only the object's previous scope."""
    if is_super_admin(user):
        return True
    if not capability or not can(user, capability):
        return False
    if obj.pk and not scope_records(type(obj).objects.all(), user, capability).filter(pk=obj.pk).exists():
        return False
    label = obj._meta.label_lower
    if obj._meta.app_label == "library":
        return can(user, "manage_library")
    if label == "registry.person":
        from apps.accounts.permissions import is_focal_person_for
        return is_focal_person_for(user, obj.unit_id)
    if label == "training.session":
        return programmes_for_creation(user).filter(pk=obj.programme_id).exists()
    if label == "training.importmappingtemplate":
        return obj.created_by_id == user.pk
    if label.startswith("programmes.") and hasattr(obj, "programme_id"):
        from apps.accounts.permissions import is_programme_owner
        return is_programme_owner(user, obj.programme_id)
    if label.startswith("training.") and hasattr(obj, "session_id"):
        from apps.accounts.permissions import session_queryset
        if not session_queryset(user, capability).filter(pk=obj.session_id).exists():
            return False
        if label == "training.attendance":
            return can_import_person(user, obj.session, obj.person)
        return True
    if label in {"passport.evidence", "passport.credential", "passport.correctionrequest"}:
        if not person_queryset(user, capability).filter(pk=obj.person_id).exists():
            return False
        if label == "passport.evidence":
            from apps.accounts.permissions import (
                scopes_for,
                session_queryset,
                unit_descendants,
            )
            for scope in scopes_for(user, capability):
                if scope.role == "focal_person" and obj.person.unit_id in unit_descendants(scope.scope_id):
                    return True
                if scope.role == "programme_owner" and obj.competency.programme_targets.filter(programme_id=scope.scope_id).exists():
                    return obj.session_id is None or obj.session.programme_id == scope.scope_id
                if scope.role == "facilitator" and obj.session_id and session_queryset(user, capability).filter(pk=obj.session_id).exists():
                    return True
            return False
        return True
    return bool(obj.pk and scope_records(type(obj).objects.all(), user, capability).filter(pk=obj.pk).exists())


class ScopedAdminPolicy:
    def _allowed(self, request, action, obj=None):
        label = self.model._meta.label_lower
        if action in {"change", "add"} and label in IMMUTABLE:
            return False
        if action == "change" and label == "passport.credential" and obj is not None and obj.person_id == person_id_for(request.user):
            return False
        capability = _capability(self.model, action)
        allowed = bool(capability and can(request.user, capability))
        if action == "view" and _reference_reader(request.user, self.model):
            allowed = True
        if not allowed:
            return False
        if obj is not None and obj.pk:
            subject_model = type(obj) if isinstance(self, InlineModelAdmin) else self.model
            if not _read_queryset(request.user, subject_model).filter(pk=obj.pk).exists():
                return False
        # Keep explicit pre-existing immutable/draft publication restrictions.
        base_method = getattr(self._policy_base, f"has_{action}_permission")
        standard = InlineModelAdmin if isinstance(self, InlineModelAdmin) else admin.ModelAdmin
        if base_method is not getattr(standard, f"has_{action}_permission"):
            if action == "view" and _reference_reader(request.user, self.model):
                return True
            if action == "add" and not isinstance(self, InlineModelAdmin):
                return base_method(self, request)
            return base_method(self, request, obj)
        return True

    def has_module_permission(self, request):
        return self._allowed(request, "view") or self._allowed(request, "add")

    def has_view_permission(self, request, obj=None):
        return self._allowed(request, "view", obj)

    def has_change_permission(self, request, obj=None):
        return self._allowed(request, "change", obj)

    def has_add_permission(self, request, obj=None):
        return self._allowed(request, "add", obj)

    def has_delete_permission(self, request, obj=None):
        # Removing a mapping is maintenance; deleting human/audit history is not.
        if self.model._meta.label_lower == "accounts.roleassignment":
            return can(request.user, "manage_users")
        if self.model._meta.label_lower not in {"programmes.programmecompetency", "programmes.programmepartner", "training.sessioncompetency", "training.sessionpartner"}:
            return False
        return self._allowed(request, "change", obj)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(pk__in=_read_queryset(request.user, self.model).values("pk"))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        related = db_field.remote_field.model
        if is_super_admin(request.user):
            kwargs["queryset"] = related.objects.all()
        elif related._meta.app_label == "library" and _reference_reader(request.user, related):
            kwargs["queryset"] = _read_queryset(request.user, related)
        elif related._meta.label_lower == "programmes.programme" and can(request.user, "create_sessions"):
            kwargs["queryset"] = programmes_for_creation(request.user)
        elif related._meta.label_lower == "registry.person":
            kwargs["queryset"] = person_queryset(request.user)
        elif related._meta.label_lower == "auth.user" and not is_super_admin(request.user):
            kwargs["queryset"] = related.objects.filter(pk=request.user.pk)
        elif related._meta.label_lower == "registry.orgunit" and can(request.user, "edit_people"):
            from apps.accounts.permissions import scopes_for, unit_descendants
            ids = set()
            for scope in scopes_for(request.user, "edit_people"):
                ids.update(unit_descendants(scope.scope_id))
            kwargs["queryset"] = related.objects.filter(pk__in=ids)
        else:
            kwargs["queryset"] = _read_queryset(request.user, related)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        kwargs["queryset"] = _read_queryset(request.user, db_field.remote_field.model)
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        capability = _capability(self.model, "change" if change else "add")
        if not _candidate_allowed(request.user, obj, capability):
            raise PermissionDenied
        if (
            obj._meta.label_lower == "passport.credential"
            and obj.review_status != "submitted"
            and (not can(request.user, "review_credentials") or obj.person_id == person_id_for(request.user))
        ):
            raise PermissionDenied
        super().save_model(request, obj, form, change)
        if obj._meta.label_lower == "training.session" and not change and can(request.user, "create_sessions"):
            from apps.sessions.models import Facilitation
            person_id = person_id_for(request.user)
            if person_id:
                Facilitation.objects.get_or_create(session=obj, person_id=person_id, defaults={"role": "lead"})

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            action = "change" if instance.pk else "add"
            if not _candidate_allowed(request.user, instance, _capability(type(instance), action)):
                raise PermissionDenied
            instance.save()
        for instance in formset.deleted_objects:
            if instance._meta.label_lower not in {"programmes.programmecompetency", "programmes.programmepartner", "training.sessioncompetency", "training.sessionpartner"} or not _candidate_allowed(request.user, instance, _capability(type(instance), "change")):
                raise PermissionDenied
            instance.delete()
        formset.save_m2m()


_wrapped = {}


def _policy_class(original):
    if issubclass(original, ScopedAdminPolicy):
        return original
    if original not in _wrapped:
        _wrapped[original] = type(f"Scoped{original.__name__}", (ScopedAdminPolicy, original), {
            "_policy_base": original,
            "inlines": tuple(_policy_class(inline) for inline in getattr(original, "inlines", ())),
            "__module__": __name__,
        })
    return _wrapped[original]


def configure_admin_policy(site):
    if getattr(site, "_suaplus_policy_installed", False):
        return
    for instance in site._registry.values():
        instance.__class__ = _policy_class(type(instance))
    original_register = site.register

    def guarded_register(self, model_or_iterable, admin_class=None, **options):
        return original_register(model_or_iterable, _policy_class(admin_class or admin.ModelAdmin), **options)

    def has_permission(self, request):
        return request.user.is_authenticated and request.user.is_active and any(
            can(request.user, capability) for capability in ("manage_users", "manage_library", "manage_programmes", "view_sessions", "view_people")
        )

    site.register = MethodType(guarded_register, site)
    site.has_permission = MethodType(has_permission, site)
    site._suaplus_policy_installed = True
