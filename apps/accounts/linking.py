"""Verified account linking uses the same canonical identity rules as attendance."""
from allauth.account.models import EmailAddress
from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.accounts.models import UserAccount
from apps.audit.services import log_event
from apps.registry.identity import (
    canonical_person,
    create_person,
    identity_data,
    identity_lock,
    match_identity,
    queue_identity,
)


def resolve_person_for_user(user, request=None):
    conflict = None
    with transaction.atomic():
        account, _ = UserAccount.objects.get_or_create(user=user)
        account = UserAccount.objects.get(pk=account.pk)
        user.hub_account = account
        if account.person_id:
            person = canonical_person(account.person)
            if person is None:
                raise PermissionDenied("This participant record requires administrator assistance.")
            if person.pk != account.person_id:
                if UserAccount.objects.filter(person=person).exclude(pk=account.pk).exists():
                    raise PermissionDenied("The canonical participant is linked to another account.")
                previous = account.person_id
                account.person = person
                account.save(update_fields=["person", "updated_at"])
                log_event(request, "account_person_reconciled", "UserAccount", account.pk,
                          before={"person_id": previous}, after={"person_id": person.pk})
            return person
        email = (user.email or "").strip()
        if not email:
            return None
        verified = EmailAddress.objects.filter(user=user, email__iexact=email, verified=True).exists()
        submitted = identity_data(email=email, full_name=user.get_full_name())
        with identity_lock(submitted):
            account = UserAccount.objects.select_for_update().get(pk=account.pk)
            user.hub_account = account
            if account.person_id:
                person = canonical_person(account.person)
                if person is None:
                    raise PermissionDenied("This participant record requires administrator assistance.")
                return person
            match = match_identity(submitted, source="account")
            if match.status in {"matched", "conflict"} and not verified:
                raise PermissionDenied("Verify your email before linking an existing participant record.")
            if match.status == "error":
                raise PermissionDenied("This identity requires administrator assistance.")
            if match.status == "conflict":
                conflict = queue_identity(match, source="account", user=user).conflict
            else:
                if match.status == "new":
                    submitted["full_name"] = user.get_full_name() or user.username or submitted["email"].split("@")[0]
                    person = create_person(submitted)
                else:
                    person = match.person
                if UserAccount.objects.filter(person=person).exclude(pk=account.pk).exists():
                    raise PermissionDenied("This participant is already linked to another account.")
                account.person = person
                account.save(update_fields=["person", "updated_at"])
                log_event(request, "account_person_linked", "UserAccount", account.pk,
                          before={"person_id": None}, after={"person_id": person.pk})
                return person
    if conflict:
        raise PermissionDenied("Your identity needs administrator review before a passport can be linked.")
    return None
