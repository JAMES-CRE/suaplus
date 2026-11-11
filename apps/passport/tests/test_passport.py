import hashlib
import secrets

import pytest
from django.urls import reverse

from apps.registry.models import PassportToken
from apps.registry.models import Person


@pytest.fixture
def person(db):
    return Person.objects.create(
        affiliation="staff",
        full_name="Kwame Mensah",
        email="km@knust.edu.gh",
        phone="+233244123456",
    )


@pytest.fixture
def passport_token(person):
    token = secrets.token_urlsafe(32)
    PassportToken.objects.create(
        person=person,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
    )
    return token


@pytest.mark.django_db
def test_passport_renders(client, passport_token, person):
    response = client.get(reverse("passport", args=[passport_token]))
    assert response.status_code == 200
    assert b"Kwame Mensah" in response.content


@pytest.mark.django_db
def test_invalid_token_returns_404(client):
    response = client.get(reverse("passport", args=["fake-token"]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_revoked_token_returns_404(client, passport_token, person):
    from django.utils import timezone

    pt = PassportToken.objects.get(person=person)
    pt.revoked_at = timezone.now()
    pt.save()

    response = client.get(reverse("passport", args=[passport_token]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_email_masked(client, passport_token):
    response = client.get(reverse("passport", args=[passport_token]))
    content = response.content.decode()
    assert "km@knust.edu.gh" not in content
    assert "k•@knust.edu.gh" in content