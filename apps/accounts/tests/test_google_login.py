from types import SimpleNamespace

import pytest
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import get_adapter
from django.http import HttpRequest
from django.test import override_settings
from django.urls import reverse

from apps.accounts.adapters import KnustSocialAccountAdapter


def _social_login(email, hosted_domain, verified=True, provider="google"):
    account = SimpleNamespace(
        provider=provider,
        extra_data={"email": email, "hd": hosted_domain},
    )
    address = SimpleNamespace(email=email, verified=verified)
    return SimpleNamespace(account=account, email_addresses=[address])


@pytest.mark.parametrize(
    "email,hosted_domain",
    [
        ("staff@knust.edu.gh", "knust.edu.gh"),
        ("student@st.knust.edu.gh", "st.knust.edu.gh"),
    ],
)
def test_verified_knust_domains_are_accepted(email, hosted_domain):
    adapter = KnustSocialAccountAdapter()

    result = adapter.pre_social_login(
        HttpRequest(), _social_login(email, hosted_domain)
    )

    assert result is None


@pytest.mark.parametrize(
    "login",
    [
        _social_login("person@gmail.com", "gmail.com"),
        _social_login("person@knust.edu.gh", "knust.edu.gh", verified=False),
        _social_login("person@knust.edu.gh", "gmail.com"),
        _social_login("person@knust.edu.gh", ""),
    ],
)
def test_non_knust_unverified_or_missing_hosted_claim_is_rejected(login):
    adapter = KnustSocialAccountAdapter()

    with pytest.raises(ImmediateHttpResponse) as exc:
        adapter.pre_social_login(HttpRequest(), login)

    assert exc.value.response.status_code == 403


def test_other_providers_are_not_changed():
    adapter = KnustSocialAccountAdapter()

    assert adapter.pre_social_login(
        HttpRequest(),
        _social_login("person@example.org", "example.org", provider="other"),
    ) is None


def test_knust_adapter_is_configured_for_allauth():
    assert isinstance(get_adapter(), KnustSocialAccountAdapter)


@pytest.mark.django_db
@override_settings(GOOGLE_OAUTH_ENABLED=False)
def test_google_login_button_stays_visible_before_credentials_are_configured(client):
    response = client.get(reverse("account_login"))

    assert response.status_code == 200
    assert b"Continue with KNUST Google" in response.content
    assert b'href="/accounts/google/login/"' in response.content
