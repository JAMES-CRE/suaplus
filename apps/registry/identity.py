"""Canonical matching, normalization, concurrency locks, and pending identities."""
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from apps.registry.identifiers import (
    normalize_email,
    normalize_name,
    normalize_number,
    normalize_phone,
    normalized_text,
)
from apps.registry.models import IdentityConflict, IdentityMutex, Organisation, Person


@dataclass
class IdentityMatch:
    status: str
    data: dict
    person: Person | None = None
    candidate_ids: tuple = ()
    reason: str = ""
    organisation_ids: tuple = ()
    conflict: IdentityConflict | None = None
    created: bool = False


def identity_data(*, full_name="", email="", phone="", staff_or_student_no="", affiliation=None, organisation_id=None, organisation_name="", age_band="unknown"):
    email = normalize_email(email)
    number = normalize_number(staff_or_student_no)
    name = normalized_text(full_name)
    if len(name) > 200 or len(number) > 30:
        raise ValidationError("Name or staff/student number is too long.")
    if age_band not in {"unknown", "under_18", "18_plus"}:
        raise ValidationError("Choose a valid age band.")
    if age_band == "under_18" and normalized_text(phone):
        raise ValidationError("Phone numbers must not be stored for minors.")
    phone = normalize_phone(phone)
    inferred = "student" if email.endswith("@st.knust.edu.gh") else "staff" if number or email.endswith("@knust.edu.gh") else "external"
    if affiliation is not None and affiliation not in {"staff", "student", "external"}:
        raise ValidationError("Choose a valid affiliation.")
    if not (number or email or phone or (name and (organisation_id or normalized_text(organisation_name)))):
        raise ValidationError("Provide an identifier, or an external participant's name and organisation.")
    return {"full_name": name, "email": email, "phone": phone, "staff_or_student_no": number,
            "affiliation": affiliation or inferred, "affiliation_explicit": affiliation is not None,
            "organisation_id": organisation_id, "organisation_name": normalized_text(organisation_name), "age_band": age_band}


def canonical_person(person):
    visited = set()
    while person is not None:
        if person.pk in visited or person.is_anonymised:
            return None
        visited.add(person.pk)
        if person.merged_into_id is None:
            return person
        person = Person.objects.filter(pk=person.merged_into_id).first()
    return None


def _candidates(queryset):
    people, names, invalid = {}, {}, []
    for alias in queryset:
        person = canonical_person(alias)
        if person is None:
            invalid.append(alias.pk)
            continue
        people[person.pk] = person
        names.setdefault(person.pk, set()).update((alias.identity_name, person.identity_name))
    return people, names, invalid


def match_identity(data, *, source="checkin", candidates=None):
    """Read-only preview. Never creates, merges, or updates a human record."""
    base = candidates if candidates is not None else Person.objects.all()
    identifiers = (("identity_number", data["staff_or_student_no"]), ("identity_email", data["email"]), ("identity_phone", data["phone"]))
    sets, people, known_names = [], {}, {}
    for attribute, value in identifiers:
        if not value:
            continue
        matches, names, invalid = _candidates(base.filter(**{attribute: value}, is_anonymised=False))
        if invalid:
            return IdentityMatch("conflict", data, candidate_ids=tuple(invalid), reason="Merged identity needs administrator reconciliation.")
        people.update(matches)
        for pk, values in names.items():
            known_names.setdefault(pk, set()).update(values)
        if matches:
            sets.append(set(matches))
    name_key = normalize_name(data["full_name"])
    if data["organisation_id"] and name_key:
        matches, names, invalid = _candidates(base.filter(identity_name=name_key, organisation_id=data["organisation_id"], affiliation="external", is_anonymised=False))
        if invalid:
            return IdentityMatch("conflict", data, candidate_ids=tuple(invalid), reason="Merged identity needs administrator reconciliation.")
        people.update(matches)
        for pk, values in names.items():
            known_names.setdefault(pk, set()).update(values)
        if matches:
            sets.append(set(matches))
    ids = set.intersection(*sets) if sets else set()
    if sets and not ids:
        return IdentityMatch("conflict", data, candidate_ids=tuple(sorted(people)), reason="The supplied identifiers refer to different people.")
    if len(ids) > 1 and name_key:
        corroborated = {pk for pk in ids if name_key in known_names.get(pk, set())}
        if corroborated:
            ids = corroborated
    if len(ids) > 1:
        return IdentityMatch("conflict", data, candidate_ids=tuple(sorted(ids)), reason="More than one person matches the supplied identity.")
    if not ids:
        retired = base.filter(is_anonymised=True).filter(
            Q(email__iexact=data["email"]) if data["email"] else Q(pk__in=[])
        )
        if data["staff_or_student_no"]:
            retired = retired | base.filter(is_anonymised=True, staff_or_student_no__iexact=data["staff_or_student_no"])
        if retired.exists():
            return IdentityMatch("error", data, reason="This identity requires administrator assistance.")
        return IdentityMatch("new", data)
    person = people[ids.pop()]
    disagreement = ""
    if (
        data["staff_or_student_no"] and person.identity_number and person.identity_number != data["staff_or_student_no"]
        and not base.filter(identity_number=data["staff_or_student_no"]).exists()
    ):
        disagreement = "The staff/student number disagrees with the existing record."
    if (
        source != "registry" and data["email"] and person.identity_email and person.identity_email != data["email"]
        and not base.filter(identity_email=data["email"]).exists()
    ):
        disagreement = "The email address disagrees with the existing record."
    if source != "registry" and data["affiliation_explicit"] and person.affiliation != data["affiliation"]:
        disagreement = "The affiliation disagrees with the existing record."
    if person.affiliation == "external" and source != "registry" and name_key and name_key not in known_names.get(person.pk, {person.identity_name}):
        disagreement = "The participant name does not match the contact's existing identity."
    if source != "registry" and data["organisation_id"] and person.organisation_id and data["organisation_id"] != person.organisation_id:
        disagreement = "The organisation disagrees with the existing record."
    if data["age_band"] != "unknown" and person.age_band != "unknown" and data["age_band"] != person.age_band:
        disagreement = "The age band disagrees with the existing record."
    if disagreement:
        return IdentityMatch("conflict", data, candidate_ids=(person.pk,), reason=disagreement)
    return IdentityMatch("matched", data, person=person, candidate_ids=(person.pk,))


def lock_hashes(data):
    values = [("number", data["staff_or_student_no"]), ("email", data["email"]), ("phone", data["phone"])]
    if data["full_name"] and data["organisation_id"]:
        values.append(("name_org", f"{data['organisation_id']}:{normalize_name(data['full_name'])}"))
    return sorted({hashlib.sha256(f"{kind}:{value}".encode()).hexdigest() for kind, value in values if value})


@contextmanager
def identity_lock(data):
    """Serialize competing writes sharing an identifier, across worker processes."""
    with transaction.atomic():
        for key in lock_hashes(data):
            IdentityMutex.objects.get_or_create(key_hash=key)
            IdentityMutex.objects.select_for_update().get(pk=key)
        yield


def create_person(data):
    if not data["full_name"]:
        raise ValidationError("A full name is required before creating a participant.")
    person = Person(
        full_name=data["full_name"], affiliation=data["affiliation"],
        email=data["email"] or None, phone=data["phone"] or None,
        staff_or_student_no=data["staff_or_student_no"] or None,
        organisation_id=data["organisation_id"], age_band=data["age_band"],
    )
    person.full_clean()
    person.save()
    return person


def queue_identity(match, *, source, session=None, context=None, user=None):
    payload = {"identity": match.data, "context": context or {}}
    stable = {"source": source, "session": session.pk if session else None, "identity": match.data}
    fingerprint = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    conflict, created = IdentityConflict.objects.get_or_create(
        fingerprint=fingerprint, status="pending",
        defaults={"source": source, "session": session, "payload": payload, "reason": match.reason,
                  "submitted_by": user if user and user.is_authenticated else None},
    )
    if created:
        conflict.candidates.set(Person.objects.filter(pk__in=match.candidate_ids))
        conflict.organisation_candidates.set(Organisation.objects.filter(pk__in=match.organisation_ids))
    match.conflict = conflict
    return match


def resolve_or_create(data, *, source="checkin", session=None, context=None, user=None, default_name=None):
    with identity_lock(data):
        match = match_identity(data, source=source)
        if match.status == "conflict":
            return queue_identity(match, source=source, session=session, context=context, user=user)
        if match.status == "new":
            if not data["full_name"] and default_name:
                data = {**data, "full_name": normalized_text(default_name)}
                match.data = data
            match.person = create_person(data)
            match.created = True
        elif match.status == "matched" and data["age_band"] == "under_18" and match.person.age_band == "unknown":
            match.person.age_band = "under_18"
            match.person.phone = None
            match.person.save(update_fields=["age_band", "phone"])
        return match


def organisation_for_name(name):
    """Reuse a uniquely named organisation, never pick the first ambiguous one."""
    name = normalized_text(name)
    if not name:
        return None, ()
    key = normalize_name(name)
    digest = hashlib.sha256(f"organisation:{key}".encode()).hexdigest()
    with transaction.atomic():
        IdentityMutex.objects.get_or_create(key_hash=digest)
        IdentityMutex.objects.select_for_update().get(pk=digest)
        rows = list(Organisation.objects.filter(name_key=key).order_by("pk")[:2])
        if len(rows) > 1:
            return None, tuple(row.pk for row in rows)
        if rows:
            return rows[0], ()
        return Organisation.objects.create(name=name, type="other"), ()
