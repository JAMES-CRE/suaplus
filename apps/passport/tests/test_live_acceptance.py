"""Drive actual sockets with CSRF/session login; isolated pytest PostgreSQL only."""

import re
from datetime import timedelta

import pytest
import requests
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import RoleAssignment, UserAccount
from apps.library.models import Competency, Domain, EvidenceRule, Framework, Level
from apps.passport.models import InternalAssessment, SelfAssessment
from apps.programmes.models import Programme, ProgrammeCompetency
from apps.registry.models import OrgUnit, Person
from apps.sessions.models import Attendance, ImportBatch, Session, SessionCompetency


@pytest.mark.django_db(transaction=True)
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEBUG=True,
    SITE_URL="http://localhost",
    WORKER_HEARTBEAT_REQUIRED=False,
)
def test_real_http_acceptance(live_server):
    base = live_server.url
    college = OrgUnit.objects.create(name="Live college", type="college")
    unit = OrgUnit.objects.create(
        name="Live department", type="department", parent=college
    )
    framework = Framework.objects.create(
        code="LIVE", name="Live framework", is_knust=True
    )
    domain = Domain.objects.create(framework=framework, code="DG", name="Live digital")
    skill = Competency.objects.create(
        framework=framework, domain=domain, code="DG-01", title="Live skill"
    )
    for ordinal in range(1, 6):
        Level.objects.create(
            framework=framework, ordinal=ordinal, label=f"Level {ordinal}"
        )
    level = framework.levels.get(ordinal=3)
    EvidenceRule.objects.create(competency=skill, evidence_type="assessor", max_level=3)
    programme = Programme.objects.create(name="Live programme")
    ProgrammeCompetency.objects.create(
        programme=programme, competency=skill, target_level=3
    )
    now = timezone.now()
    workshop = Session.objects.create(
        programme=programme,
        title="Live workshop",
        start_at=now - timedelta(hours=2),
        end_at=now - timedelta(hours=1),
        mode="in_person",
        status="closed",
        host_unit=unit,
    )
    SessionCompetency.objects.create(session=workshop, competency=skill)

    def post(browser, path, data=None, files=None):
        response = browser.get(base + path, timeout=15)
        assert response.status_code == 200, (path, response.status_code)
        csrf = browser.cookies.get("csrftoken")
        assert csrf
        return browser.post(
            base + path,
            data={**(data or {}), "csrfmiddlewaretoken": csrf},
            files=files,
            headers={"Referer": base + path},
            timeout=15,
        )

    public = requests.Session()
    assert public.get(base + "/live/", timeout=15).status_code == 200
    assert public.get(base + "/ready/", timeout=15).status_code == 200
    participant = Person.objects.create(
        full_name="Live participant",
        affiliation="staff",
        category="academic",
        email="live-participant@knust.edu.gh",
        unit=unit,
    )
    assert post(public, "/sas/", {"email": participant.email}).status_code == 200
    message = next(
        item for item in mail.outbox if "Self-Assessment Link" in item.subject
    )
    token = re.search(r"/sas/confirm/([^/]+)/", message.body).group(1)
    sas = "/sas/confirm/" + token + "/"
    assert (
        post(
            public,
            sas,
            {
                "full_name": participant.full_name,
                "title": "Dr.",
                "category": "academic",
                "unit": str(unit.pk),
                "phone": "",
                "age_band": "unknown",
                "consent": "on",
            },
        ).status_code
        == 200
    )
    assert (
        post(public, sas, {"consent": "on", f"comp_{skill.pk}": "3"}).status_code == 200
    )
    assert SelfAssessment.objects.filter(person=participant).count() == 1
    assert public.get(base + sas, timeout=15).status_code == 400

    user = get_user_model().objects.create_user(
        username="live-root",
        email="live-root@knust.edu.gh",
        password="Local-live-test-only-1283",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    admin = requests.Session()
    response = post(
        admin,
        "/accounts/login/",
        {"login": user.username, "password": "Local-live-test-only-1283"},
    )
    assert "/account/totp/setup/" in response.url
    device = TOTPDevice.objects.get(user=user)
    code = str(
        totp(
            device.bin_key,
            step=device.step,
            t0=device.t0,
            digits=device.digits,
            drift=device.drift,
        )
    ).zfill(device.digits)
    assert (
        post(
            admin, "/account/totp/setup/", {"token": code, "next": "/hub/"}
        ).status_code
        == 200
    )
    assert admin.get(base + "/dashboard/", timeout=15).status_code == 200
    assert (
        admin.get(base + "/dashboard/?export=csv", timeout=15)
        .headers["Content-Type"]
        .startswith("text/csv")
    )
    response = post(
        admin,
        "/import/upload/",
        {"session": workshop.pk},
        {
            "file": (
                "participants.csv",
                b"Name,Email\nImported live,imported-live@knust.edu.gh\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    assert (
        post(
            admin, "/import/preview/", {"name_col": "Name", "email_col": "Email"}
        ).status_code
        == 200
    )
    assert post(admin, "/import/commit/").status_code == 200
    batch = ImportBatch.objects.get()
    # Undo has a dedicated confirmation page; POST is CSRF-protected.
    assert admin.get(base + f"/import/batch/{batch.pk}/", timeout=15).status_code == 200
    csrf = admin.cookies.get("csrftoken")
    assert (
        admin.post(
            base + f"/import/batch/{batch.pk}/undo/",
            data={"reason": "Live acceptance reversal", "csrfmiddlewaretoken": csrf},
            headers={"Referer": base + f"/import/batch/{batch.pk}/"},
            timeout=15,
        ).status_code
        == 200
    )
    batch.refresh_from_db()
    assert batch.status == "undone"
    assert Attendance.objects.get(import_batch=batch).status == "withdrawn"
    attendance = Attendance.objects.create(
        person=participant,
        session=workshop,
        method="manual",
        status="completed",
        check_in_at=workshop.start_at,
        check_out_at=workshop.end_at,
    )
    data = {
        "competency": skill.pk,
        "level": level.pk,
        "evidence_type": "assessor",
        "note": "Live acceptance observed task",
    }
    assert (
        post(admin, f"/credentials/internal/{attendance.pk}/", data).status_code == 200
    )
    assert InternalAssessment.objects.filter(attendance=attendance).count() == 1
    assert (
        post(admin, f"/credentials/internal/{attendance.pk}/", data).status_code == 200
    )
    assert InternalAssessment.objects.filter(attendance=attendance).count() == 1
