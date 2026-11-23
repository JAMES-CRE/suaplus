import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.passport.models import Credential
from apps.passport.services import generate_badge_json, verify_badge
from apps.reporting.test_calculations import graph as graph_fixture

graph = graph_fixture


@override_settings(DEBUG=False, BADGE_PRIVATE_KEY="", BADGE_PUBLIC_KEY="")
def test_production_signing_stays_disabled_without_dedicated_keys(graph):
    assert generate_badge_json(graph.person, graph.skill, graph.levels[3]) is None


@override_settings(DEBUG=True, BADGE_PRIVATE_KEY="", BADGE_PUBLIC_KEY="")
def test_local_badges_are_not_accepted_in_production(graph):
    signed = generate_badge_json(graph.person, graph.skill, graph.levels[3])
    assert verify_badge(signed)
    with override_settings(DEBUG=False):
        assert verify_badge(signed) is None


@override_settings(DEBUG=True)
def test_revoked_credential_badge_is_invalid_on_public_page(client, graph):
    signed = generate_badge_json(graph.person, graph.skill, graph.levels[3])
    Credential.objects.create(
        person=graph.person,
        kind="knust_badge",
        source="staff_recorded",
        title="Revoked badge",
        issued_at=timezone.localdate(),
        review_status="approved",
        status="revoked",
        badge_json={"jwt": signed},
    )
    response = client.get(reverse("badge_verify", args=[signed]))
    assert "public/badge_invalid.html" in [
        template.name for template in response.templates
    ]


def test_asymmetric_badge_roundtrip_when_crypto_dependency_available(graph):
    # Local dependency installation is permission-blocked; CI installs declared crypto extra.
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    from cryptography.hazmat.primitives import serialization

    private = crypto.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    with override_settings(
        DEBUG=False, BADGE_PRIVATE_KEY=private_pem, BADGE_PUBLIC_KEY=public_pem
    ):
        signed = generate_badge_json(graph.person, graph.skill, graph.levels[3])
        assert verify_badge(signed)["credentialSubject"]["achievement"][
            "name"
        ].startswith(graph.skill.code)
