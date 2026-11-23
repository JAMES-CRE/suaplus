import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from apps.core.tasks import worker_heartbeat


@pytest.mark.django_db
@override_settings(WORKER_HEARTBEAT_REQUIRED=True)
def test_readiness_requires_executed_worker_heartbeat(client):
    cache.delete("worker:heartbeat")
    assert client.get(reverse("liveness")).status_code == 200
    response = client.get(reverse("readiness"))
    assert response.status_code == 503
    assert response.json()["worker"] is False
    worker_heartbeat()
    assert client.get(reverse("readiness")).status_code == 200
    cache.delete("worker:heartbeat")


@pytest.mark.django_db
@override_settings(WORKER_HEARTBEAT_REQUIRED=False)
def test_ready_without_worker_requirement(client):
    assert client.get(reverse("readiness")).status_code == 200


def test_rate_limit_fixed_window_atomic_counter():
    from django.http import HttpResponse
    from django.test import RequestFactory

    from apps.core.rate_limit import rate_limit

    cache.clear()
    view = rate_limit("test_atomic", max_requests=2, window_seconds=60)(
        lambda request: HttpResponse("ok")
    )
    request = RequestFactory().get("/")
    assert view(request).status_code == 200
    assert view(request).status_code == 200
    response = view(request)
    assert response.status_code == 429
    assert response["Retry-After"] == "60"


def test_operational_logs_redact_bearer_urls():
    import logging

    from apps.core.logging import RedactBearerPaths

    for prefix in ["p", "sas/confirm", "followup", "badges/verify"]:
        record = logging.LogRecord(
            "test",
            logging.WARNING,
            "",
            1,
            "Failed %s",
            ("/" + prefix + "/secret-token/",),
            None,
        )
        assert RedactBearerPaths().filter(record)
        assert "secret-token" not in record.getMessage()
        assert "[redacted]" in record.getMessage()
