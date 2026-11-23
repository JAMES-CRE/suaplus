import hashlib
from datetime import date
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from apps.library.models import Competency, Domain, Framework, Level
from apps.passport.models import Credential, Evidence
from apps.registry.models import PassportToken, Person


class PassportLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "a" and attrs.get("href", "").startswith("#"):
            self.anchors.append(attrs["href"][1:])


@pytest.fixture
def passport_person(db):
    person = Person.objects.create(
        full_name="Passport user", email="passport-ui@knust.edu.gh", affiliation="staff"
    )
    token = "passport-ui-test-token"
    PassportToken.objects.create(
        person=person, token_hash=hashlib.sha256(token.encode()).hexdigest()
    )
    return person, reverse("passport", args=[token])


@pytest.mark.django_db
def test_empty_passport_guides_staff_to_assessment(client, passport_person):
    _, url = passport_person
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["next_action"]["url"] == reverse("sas_start")
    assert b"Start with your self-assessment" in response.content
    assert response.context["profile_summary"]["credential_count"] == 0


@pytest.mark.django_db
def test_returned_credential_is_prioritized_and_anonymous_action_explains_sign_in(
    client, passport_person
):
    person, url = passport_person
    credential = Credential.objects.create(
        person=person,
        title="Certificate needing a correction",
        kind="external",
        source="self_submitted",
        review_status="returned",
        issued_at=date.today(),
    )
    response = client.get(url)
    action = response.context["next_action"]
    assert action["url"] == f"{reverse('credential_submit')}?resubmit={credential.id}"
    assert action["label"] == "Sign in to update credential"


@pytest.mark.django_db
def test_summary_counts_only_approved_active_credentials(client, passport_person):
    person, url = passport_person
    for review, status in [
        ("approved", "valid"),
        ("approved", "revoked"),
        ("submitted", "valid"),
    ]:
        Credential.objects.create(
            person=person,
            title="Certificate",
            kind="external",
            issued_at=date.today(),
            review_status=review,
            status=status,
        )
    response = client.get(url)
    assert response.context["profile_summary"]["credential_count"] == 1


@pytest.mark.django_db
def test_passport_section_links_have_unique_existing_destinations(
    client, passport_person
):
    _, url = passport_person
    response = client.get(url)
    links = PassportLinks()
    links.feed(response.content.decode())
    assert len(links.ids) == len(set(links.ids))
    assert set(links.anchors).issubset(set(links.ids))


@pytest.mark.django_db
def test_external_empty_passport_guides_to_a_learning_goal(client, passport_person):
    person, url = passport_person
    person.affiliation = "external"
    person.save(update_fields=["affiliation"])
    response = client.get(url)
    assert response.context["next_action"]["url"] == "#learning-goals"


@pytest.mark.django_db
def test_higher_self_rating_does_not_hide_verified_skill(client, passport_person):
    person, url = passport_person
    framework = Framework.objects.create(code="UI", name="UI framework", is_knust=True)
    domain = Domain.objects.create(
        framework=framework, code="D1", name="Digital skills"
    )
    competency = Competency.objects.create(
        framework=framework, domain=domain, code="UI.1", title="Digital collaboration"
    )
    for evidence_type, ordinal in [("self", 4), ("task", 2)]:
        level = Level.objects.create(
            framework=framework, ordinal=ordinal, label=f"Level {ordinal}"
        )
        Evidence.objects.create(
            person=person,
            competency=competency,
            level=level,
            type=evidence_type,
            source_type="self_assessment" if evidence_type == "self" else "attendance",
        )
    response = client.get(url)
    data = response.context["domains"][domain]
    assert data["self_percent"] == 80.0
    assert data["verified_percent"] == 40.0
    assert response.context["profile_summary"]["verified_count"] == 1
