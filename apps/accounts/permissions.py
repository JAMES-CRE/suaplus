"""Role-qualified SRS capabilities. Scopes are additive, never permissions."""
from dataclasses import dataclass
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.accounts.models import RoleAssignment

ROLE_SCOPES = {
    "participant": {"own"}, "associate_trainer": {"session", "programme"},
    "facilitator": {"session", "programme"}, "partner_coordinator": {"organisation"},
    "focal_person": {"unit"}, "programme_owner": {"programme"},
    "executive_viewer": {"all"}, "super_admin": {"all"}, "library_curator": {"all"},
}
OPERATIONAL = {"facilitator", "programme_owner", "focal_person"}
PERSONAL_READERS = OPERATIONAL | {"associate_trainer", "partner_coordinator"}
CAPABILITIES = {
    "view_passport": set(),
    "manage_own_passport": set(),
    "view_sessions": OPERATIONAL | {"associate_trainer"},
    "create_sessions": {"facilitator", "programme_owner"},
    "manage_sessions": {"facilitator", "programme_owner"},
    "import_attendance": OPERATIONAL, "edit_records": OPERATIONAL,
    "view_people": PERSONAL_READERS, "edit_people": {"focal_person"},
    "nominate_trainers": OPERATIONAL, "approve_trainers": {"programme_owner"},
    "manage_programmes": {"programme_owner"},
    "review_credentials": {"focal_person", "programme_owner"},
    "record_credentials": OPERATIONAL,
    "view_dashboard": OPERATIONAL | {"partner_coordinator", "executive_viewer"},
    "export_reports": {"partner_coordinator", "focal_person", "programme_owner", "executive_viewer"},
    "manage_library": {"library_curator"},
    "manage_users": set(), "manage_units": set(), "manage_targets": set(),
    "manage_calendar": set(), "manage_notifications": set(),
    "audit": set(), "data_subject_requests": set(),
}

@dataclass(frozen=True)
class Scope:
    role: str
    scope_type: str
    scope_id: int | None

    def matches_unit(self, unit_id):
        return self.scope_type == "unit" and unit_id in unit_descendants(self.scope_id)

    def matches_programme(self, programme_id):
        return self.scope_type == "programme" and self.scope_id == programme_id


def get_user_scopes(user):
    if not user or not user.is_authenticated:
        return []
    try:
        account = user.hub_account
    except AttributeError:
        return []
    scopes = []
    for role in RoleAssignment.objects.filter(user_account=account):
        if role.scope_type not in ROLE_SCOPES.get(role.role, set()):
            continue
        if role.scope_type in {"own", "all"}:
            if role.scope_id is not None:
                continue
        elif role.scope_id is None:
            continue
        scopes.append(Scope(role.role, role.scope_type, role.scope_id))
    return scopes


def is_super_admin(user):
    return bool(user and user.is_authenticated and (
        user.is_superuser or any(s.role == "super_admin" for s in get_user_scopes(user))
    ))


def can(user, capability):
    if capability in {"view_passport", "manage_own_passport"}:
        return bool(user and user.is_authenticated)
    return capability in CAPABILITIES and (
        is_super_admin(user) or any(s.role in CAPABILITIES[capability] for s in get_user_scopes(user))
    )


def require_capability(capability):
    def decorator(view):
        @wraps(view)
        def checked(request, *args, **kwargs):
            if not can(request.user, capability):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return checked
    return decorator


def scopes_for(user, capability):
    return [s for s in get_user_scopes(user) if s.role in CAPABILITIES.get(capability, set())]


def person_id_for(user):
    try:
        return user.hub_account.person_id
    except AttributeError:
        return None


def unit_descendants(unit_id):
    from apps.registry.models import OrgUnit
    parents = dict(OrgUnit.objects.values_list("pk", "parent_id"))
    if unit_id not in parents:
        return set()
    found, frontier = {unit_id}, {unit_id}
    while frontier:
        frontier = {pk for pk, parent in parents.items() if parent in frontier} - found
        found.update(frontier)
    return found


def is_library_curator(user):
    return can(user, "manage_library")


def is_programme_owner(user, programme_id=None):
    return is_super_admin(user) or any(
        s.role == "programme_owner" and (programme_id is None or s.matches_programme(programme_id))
        for s in get_user_scopes(user)
    )


def is_focal_person_for(user, unit_id):
    return is_super_admin(user) or any(s.role == "focal_person" and s.matches_unit(unit_id) for s in get_user_scopes(user))


def is_executive(user):
    return any(s.role == "executive_viewer" for s in get_user_scopes(user))


def aggregate_only(user):
    return is_executive(user) and not can(user, "view_people")


def session_queryset(user, capability="view_sessions"):
    from apps.sessions.models import Session
    qs = Session.objects.all()
    if is_super_admin(user):
        return qs
    allowed, person_id = Q(pk__in=[]), person_id_for(user)
    for scope in scopes_for(user, capability):
        if scope.role == "executive_viewer":
            return qs
        if scope.role == "programme_owner":
            allowed |= Q(programme_id=scope.scope_id)
        elif scope.role in {"facilitator", "associate_trainer"}:
            if scope.scope_type == "session":
                allowed |= Q(pk=scope.scope_id)
            elif person_id:
                allowed |= Q(programme_id=scope.scope_id, facilitators__person_id=person_id)
        elif scope.role == "focal_person":
            units = unit_descendants(scope.scope_id)
            allowed |= Q(host_unit_id__in=units) | Q(attendances__person__unit_id__in=units)
        elif scope.role == "partner_coordinator":
            allowed |= Q(attendances__person__organisation_id=scope.scope_id)
    return qs.filter(allowed).distinct()


def programmes_for_creation(user):
    from apps.programmes.models import Programme
    from apps.sessions.models import Session
    if is_super_admin(user):
        return Programme.objects.all()
    allowed = Q(pk__in=[])
    for scope in scopes_for(user, "create_sessions"):
        if scope.scope_type == "programme":
            allowed |= Q(pk=scope.scope_id)
        elif scope.scope_type == "session":
            allowed |= Q(pk__in=Session.objects.filter(pk=scope.scope_id).values("programme_id"))
    return Programme.objects.filter(allowed).distinct()


def person_queryset(user, capability="view_people"):
    from apps.registry.models import Person
    if is_super_admin(user):
        return Person.objects.all()
    allowed = Q(pk__in=[])
    for scope in scopes_for(user, capability):
        if scope.role == "executive_viewer":
            return Person.objects.filter(is_anonymised=False, merged_into__isnull=True)
        if scope.role == "focal_person":
            allowed |= Q(unit_id__in=unit_descendants(scope.scope_id))
        elif scope.role == "partner_coordinator":
            allowed |= Q(organisation_id=scope.scope_id)
        elif scope.role == "programme_owner":
            allowed |= Q(attendances__session__programme_id=scope.scope_id) | Q(tier_statuses__programme_id=scope.scope_id)
        elif scope.role in {"facilitator", "associate_trainer"}:
            allowed |= Q(attendances__session_id__in=session_queryset(user, capability).values("pk"))
            if capability in {"view_people", "record_credentials"} and person_id_for(user):
                allowed |= Q(pk=person_id_for(user))
    return Person.objects.filter(allowed, is_anonymised=False, merged_into__isnull=True).distinct()


def credential_queryset(user, capability="review_credentials"):
    from apps.passport.models import Credential
    if is_super_admin(user):
        return Credential.objects.all()
    allowed = Q(pk__in=[])
    for scope in scopes_for(user, capability):
        if scope.role == "focal_person":
            allowed |= Q(person__unit_id__in=unit_descendants(scope.scope_id))
        elif scope.role == "programme_owner":
            members = Q(person__attendances__session__programme_id=scope.scope_id) | Q(person__tier_statuses__programme_id=scope.scope_id)
            allowed |= members & (Q(catalogue__isnull=True) | Q(catalogue__competencies__competency__programme_targets__programme_id=scope.scope_id))
        elif scope.role == "partner_coordinator":
            allowed |= Q(person__organisation_id=scope.scope_id)
        elif scope.role in {"facilitator", "associate_trainer"}:
            allowed |= Q(person_id=person_id_for(user))
        elif scope.role == "executive_viewer":
            return Credential.objects.all()
    return Credential.objects.filter(allowed, person__is_anonymised=False, person__merged_into__isnull=True).distinct()


def visible_memos(user):
    from apps.reporting.models import Memo
    qs = Memo.objects.all()
    if is_super_admin(user):
        return qs
    if not can(user, "export_reports"):
        return qs.none()
    qs = qs.filter(scope__aggregate_only=True)
    if is_executive(user):
        return qs
    current = {(s.role, s.scope_type, s.scope_id) for s in scopes_for(user, "export_reports")}
    ids = []
    for memo in qs.only("pk", "scope"):
        saved = memo.scope.get("access_scopes", [])
        captured = {(s.get("role"), s.get("scope_type"), s.get("scope_id")) for s in saved}
        if captured and captured <= current:
            ids.append(memo.pk)
    return qs.filter(pk__in=ids)


def scope_records(queryset, user, capability="view_people"):
    if not can(user, capability):
        return queryset.none()
    if is_super_admin(user):
        return queryset
    model = queryset.model._meta.label_lower
    if capability in {"view_passport", "manage_own_passport"}:
        person_id = person_id_for(user)
        if person_id is None:
            return queryset.none()
        if model == "registry.person":
            return queryset.filter(pk=person_id, is_anonymised=False, merged_into__isnull=True)
        if any(field.name == "person" for field in queryset.model._meta.fields):
            return queryset.filter(person_id=person_id)
        return queryset.none()
    if model == "reporting.memo":
        return queryset.filter(pk__in=visible_memos(user).values("pk"))
    if capability in {"view_dashboard", "export_reports"} and is_executive(user):
        return queryset
    scopes = scopes_for(user, capability)
    sessions, people = session_queryset(user, capability), person_queryset(user, capability)
    allowed = Q(pk__in=[])
    if model == "registry.person":
        return queryset.filter(pk__in=people.values("pk"))
    if model == "training.session":
        return queryset.filter(pk__in=sessions.values("pk"))
    if model == "passport.credential":
        return queryset.filter(pk__in=credential_queryset(user, capability).values("pk"))
    if model in {"passport.selfassessment", "passport.learninggoal", "passport.correctionrequest", "registry.consent"}:
        return queryset.filter(person_id__in=people.values("pk"))
    if model in {"training.surveyresponse", "training.facilitation"}:
        from apps.sessions.models import Attendance
        rows = scope_records(Attendance.objects.all(), user, capability)
        if model == "training.surveyresponse":
            return queryset.filter(attendance_id__in=rows.values("pk"))
        return queryset.filter(session_id__in=sessions.values("pk"), session__attendances__id__in=rows.values("pk")).distinct()
    if model in {"training.sessioncompetency", "training.sessionpartner", "training.importbatch"}:
        return queryset.filter(session_id__in=sessions.values("pk"))
    if model == "training.importmappingtemplate":
        return queryset.filter(created_by=user)
    if model in {"programmes.programmecompetency", "programmes.programmepartner", "programmes.tiercriterion"}:
        from apps.programmes.models import Programme
        programmes = scope_records(Programme.objects.all(), user, capability)
        return queryset.filter(programme_id__in=programmes.values("pk"))
    if model == "programmes.programme":
        for scope in scopes:
            if scope.role == "programme_owner":
                allowed |= Q(pk=scope.scope_id)
        return queryset.filter(allowed | Q(sessions__in=sessions)).distinct()
    if model in {"registry.orgunit", "registry.unitheadcount"}:
        units = set(people.exclude(unit_id=None).values_list("unit_id", flat=True))
        for scope in scopes:
            if scope.role == "focal_person":
                units.update(unit_descendants(scope.scope_id))
        if model == "registry.orgunit":
            units.update(sessions.exclude(host_unit_id=None).values_list("host_unit_id", flat=True))
            from apps.registry.models import OrgUnit
            parents, frontier = dict(OrgUnit.objects.values_list("pk", "parent_id")), set(units)
            while frontier:
                frontier = {parents.get(pk) for pk in frontier} - units - {None}
                units.update(frontier)
        return queryset.filter(**{f"{'pk' if model == 'registry.orgunit' else 'unit_id'}__in": units})
    if model == "registry.organisation":
        allowed |= Q(pk__in=people.exclude(organisation_id=None).values("organisation_id"))
        for scope in scopes:
            if scope.role == "partner_coordinator":
                allowed |= Q(pk=scope.scope_id)
        return queryset.filter(allowed).distinct()
    if model == "programmes.partner":
        for scope in scopes:
            if scope.role == "programme_owner":
                allowed |= Q(programmes__programme_id=scope.scope_id)
            elif scope.role == "partner_coordinator":
                allowed |= Q(organisation_id=scope.scope_id)
        if any(s.role in OPERATIONAL for s in scopes):
            allowed |= Q(sessions__session_id__in=sessions.values("pk")) | Q(programmes__programme__sessions__pk__in=sessions.values("pk"))
        return queryset.filter(allowed).distinct()
    if model in {"training.attendance", "passport.evidence", "programmes.tierstatus", "programmes.nomination"}:
        for scope in scopes:
            if scope.role == "focal_person":
                allowed |= Q(person__unit_id__in=unit_descendants(scope.scope_id))
            elif scope.role == "partner_coordinator":
                allowed |= Q(person__organisation_id=scope.scope_id)
            elif scope.role == "programme_owner":
                if model == "passport.evidence":
                    members = Q(person__attendances__session__programme_id=scope.scope_id) | Q(person__tier_statuses__programme_id=scope.scope_id)
                    allowed |= members & (Q(session__programme_id=scope.scope_id) | Q(session__isnull=True, competency__programme_targets__programme_id=scope.scope_id))
                else:
                    field = "session__programme_id" if model == "training.attendance" else "programme_id"
                    allowed |= Q(**{field: scope.scope_id})
            elif scope.role in {"facilitator", "associate_trainer"}:
                if model in {"training.attendance", "passport.evidence"}:
                    allowed |= Q(session_id__in=sessions.values("pk"))
                else:
                    allowed |= Q(person_id__in=people.values("pk"), programme_id__in=sessions.values("programme_id"))
        return queryset.filter(allowed).distinct()
    if model.startswith("library.") and capability == "manage_library":
        return queryset
    return queryset.none()


def scope_queryset(queryset, user, field_map, capability="view_people"):
    if not can(user, capability):
        return queryset.none()
    if is_super_admin(user):
        return queryset
    allowed = Q(pk__in=[])
    for scope in scopes_for(user, capability):
        field = field_map.get(scope.scope_type)
        if field and scope.scope_id is not None:
            ids = unit_descendants(scope.scope_id) if scope.scope_type == "unit" else {scope.scope_id}
            allowed |= Q(**{f"{field}__in": ids})
    return queryset.filter(allowed).distinct()


def can_view_person(user, person_id):
    return person_queryset(user).filter(pk=person_id).exists()


def can_import_person(user, session, person):
    if is_super_admin(user):
        return True
    for scope in scopes_for(user, "import_attendance"):
        if scope.role == "programme_owner" and scope.scope_id == session.programme_id:
            return True
        if scope.role == "facilitator":
            if scope.scope_type == "session" and scope.scope_id == session.pk:
                return True
            if scope.scope_type == "programme" and scope.scope_id == session.programme_id and session.facilitators.filter(person_id=person_id_for(user)).exists():
                return True
        if scope.role == "focal_person" and person and person.unit_id in unit_descendants(scope.scope_id):
            return True
    return False


def trainer_status_queryset(user, capability="nominate_trainers"):
    from apps.programmes.models import TierStatus
    return scope_records(TierStatus.objects.all(), user, capability)
