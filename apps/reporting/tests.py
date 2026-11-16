from django.test import TestCase

# Create your tests here.
import pytest
import datetime as dt
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.urls import reverse

from apps.accounts.models import RoleAssignment, UserAccount
from apps.programmes.models import Partner, Programme, ProgrammePartner
from apps.reporting.exports import suppress_small_counts
from apps.reporting.models import ExportLog
from apps.reporting.views import _resolve_dashboard_period
from apps.core.models import AcademicPeriod
from apps.registry.models import Organisation, Person
from apps.sessions.models import Attendance, Session, SessionPartner


@pytest.mark.django_db
def test_dashboard_rejects_out_of_scope_programme_filter(client):
    user = get_user_model().objects.create_user(
        username="owner@knust.edu.gh",
        email="owner@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    allowed = Programme.objects.create(name="Allowed Programme")
    forbidden = Programme.objects.create(name="Other Programme")
    RoleAssignment.objects.create(
        user_account=account,
        role="programme_owner",
        scope_type="programme",
        scope_id=allowed.id,
    )
    client.force_login(user)

    response = client.get(
        reverse("dashboard"), {"programme": forbidden.id}
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_dashboard_limits_programme_choices_to_owner_scope(client):
    user = get_user_model().objects.create_user(
        username="owner2@knust.edu.gh",
        email="owner2@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    allowed = Programme.objects.create(name="Scoped Programme")
    Programme.objects.create(name="Unrelated Programme")
    RoleAssignment.objects.create(
        user_account=account,
        role="programme_owner",
        scope_type="programme",
        scope_id=allowed.id,
    )
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert list(response.context["programmes"]) == [allowed]


@pytest.mark.django_db
def test_dashboard_exports_csv_xlsx_and_pdf_and_logs_each_export(client):
    user = get_user_model().objects.create_user(
        username="export-admin@knust.edu.gh",
        email="export-admin@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account,
        role="super_admin",
        scope_type="all",
    )
    client.force_login(user)

    for export_format, content_type, signature in [
        ("csv", "text/csv", b"\xef\xbb\xbfSection,Metric,Value"),
        (
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK",
        ),
        ("pdf", "application/pdf", b"%PDF-1.4"),
    ]:
        response = client.get(
            reverse("dashboard"),
            {"period": "90d", "export": export_format},
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith(content_type)
        assert response.content.startswith(signature)

    from apps.reporting.models import ExportLog

    assert ExportLog.objects.filter(actor=user, view_name="dashboard").count() == 3
    assert set(
        ExportLog.objects.filter(actor=user).values_list("format", flat=True)
    ) == {"csv", "xlsx", "pdf"}
    assert all(
        log.filters["period"] == "90d"
        for log in ExportLog.objects.filter(actor=user)
    )


@pytest.mark.django_db
def test_drilldown_and_memo_exports_are_logged(client):
    from apps.reporting.models import ExportLog, Memo

    user = get_user_model().objects.create_user(
        username="report-export-admin@knust.edu.gh",
        email="report-export-admin@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    memo = Memo.objects.create(
        period_label="Test Period",
        scope={},
        figures={"reach": 10},
        narrative="Executive summary line.",
        created_by=user,
    )
    client.force_login(user)

    drilldown_response = client.get(
        reverse("drilldown", args=["reach"]), {"export": "csv"}
    )
    memo_response = client.get(
        reverse("memo_detail", args=[memo.id]), {"export": "xlsx"}
    )

    assert drilldown_response.status_code == 200
    assert drilldown_response["Content-Type"].startswith("text/csv")
    assert memo_response.status_code == 200
    assert memo_response["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert ExportLog.objects.filter(actor=user).count() == 2


def test_suppress_small_counts_redacts_counts_and_derived_aggregates():
    data = suppress_small_counts(
        {
            "reach": 4,
            "csat": {"count": 2, "mean": 4.5},
            "pipeline": {"Explorer": 1, "Certified": 7},
            "reach_by_unit": [{"unit": "Engineering", "people": 3}],
            "ai_coverage": [{"unit": "Science", "people": 2, "total": 25, "percent": 8}],
        }
    )

    assert data["reach"] == "<5"
    assert data["csat"] == {"count": "<5", "mean": "<5"}
    assert data["pipeline"] == {"Explorer": "<5", "Certified": 7}
    assert data["reach_by_unit"][0]["people"] == "<5"
    assert data["reach_by_unit"][0]["chart_value"] is None
    assert data["ai_coverage"][0]["percent"] == "<5"
    assert data["ai_coverage"][0]["chart_value"] is None


@pytest.mark.django_db
def test_executive_dashboard_suppresses_small_counts_in_view_and_export(client):
    user = get_user_model().objects.create_user(
        username="executive-viewer@knust.edu.gh",
        email="executive-viewer@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="executive_viewer", scope_type="all"
    )
    client.force_login(user)

    page = client.get(reverse("dashboard"))
    export = client.get(reverse("dashboard"), {"export": "csv"})

    assert page.status_code == 200
    assert page.context["reach"] == "<5"
    assert page.context["workshops_held"] == "<5"
    assert page.context["pipeline"]["Explorer"] == "<5"
    assert export.status_code == 200
    assert b"KPI,Reach,<5" in export.content
    assert ExportLog.objects.filter(actor=user, view_name="dashboard").exists()


@pytest.mark.django_db
def test_executive_drilldown_hides_fewer_than_five_people(client):
    user = get_user_model().objects.create_user(
        username="executive-drilldown@knust.edu.gh",
        email="executive-drilldown@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="executive_viewer", scope_type="all"
    )
    programme = Programme.objects.create(name="Small group programme")
    session = Session.objects.create(
        programme=programme,
        title="Small group session",
        start_at=dt.datetime(2026, 10, 1, 9, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 10, 1, 17, tzinfo=dt.timezone.utc),
        mode="in_person",
        status="closed",
    )
    person = Person.objects.create(
        affiliation="staff", full_name="Private Small Group Member"
    )
    Attendance.objects.create(
        session=session,
        person=person,
        method="manual",
        check_in_at=session.start_at,
        status="partial",
    )
    client.force_login(user)

    response = client.get(reverse("drilldown", args=["reach"]))

    assert response.status_code == 200
    assert response.context["small_group_suppressed"] is True
    assert response.context["rows"] == []
    assert b"Private Small Group Member" not in response.content


@pytest.mark.django_db
def test_dashboard_chart_json_scripts_render_outside_executable_script(client, monkeypatch):
    user = get_user_model().objects.create_user(
        username="chart-admin@knust.edu.gh",
        email="chart-admin@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    monkeypatch.setattr(
        "apps.reporting.views.kpi_ai_coverage_by_college",
        lambda **kwargs: [{"unit": "Science", "people": 8, "total": 10, "percent": 80}],
    )
    monkeypatch.setattr(
        "apps.reporting.views.kpi_attendances_by_month",
        lambda **kwargs: [{"month": "Nov", "count": 9}],
    )
    monkeypatch.setattr(
        "apps.reporting.views.kpi_reach_by_unit",
        lambda **kwargs: [{"unit": "Science", "people": 9}],
    )
    client.force_login(user)

    response = client.get(reverse("dashboard"))
    html = response.content.decode()

    assert response.status_code == 200
    for data_id, variable in [
        ("coverage-data", "var coverageData"),
        ("monthly-reach-data", "var monthlyData"),
        ("reach-by-unit-data", "var reachData"),
    ]:
        data_position = html.index(f'id="{data_id}"')
        variable_position = html.index(variable)
        executable_start = html.rfind("<script", 0, variable_position)
        executable_end = html.index("</script>", executable_start)
        assert data_position < executable_start
        assert executable_start < variable_position < executable_end


@pytest.mark.django_db
def test_dashboard_period_resolver_supports_academic_quarter_and_custom_ranges():
    academic_period = AcademicPeriod.objects.create(
        year_start=2025,
        year_end=2026,
        semester=1,
        start_date="2025-08-01",
        end_date="2025-12-20",
    )
    programme = Programme.objects.create(name="Period test programme")
    Session.objects.create(
        programme=programme,
        title="Period test session",
        start_at=dt.datetime(2026, 4, 20, 9, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 4, 20, 17, tzinfo=dt.timezone.utc),
        mode="in_person",
    )

    academic = _resolve_dashboard_period(
        type("Request", (), {"GET": QueryDict(f"period=academic-{academic_period.id}")})()
    )
    quarter = _resolve_dashboard_period(
        type("Request", (), {"GET": QueryDict("period=quarter-2026-Q2")})()
    )
    custom = _resolve_dashboard_period(
        type("Request", (), {"GET": QueryDict("period=custom&date_from=2026-02-03&date_to=2026-02-10")})()
    )

    assert (academic["since"].date(), academic["until"].date()) == (
        dt.date(2025, 8, 1), dt.date(2025, 12, 20)
    )
    assert (quarter["since"].date(), quarter["until"].date()) == (
        dt.date(2026, 4, 1), dt.date(2026, 6, 30)
    )
    assert (custom["since"].date(), custom["until"].date()) == (
        dt.date(2026, 2, 3), dt.date(2026, 2, 10)
    )
    assert not academic["period_error"]
    assert not quarter["period_error"]
    assert not custom["period_error"]


@pytest.mark.django_db
def test_dashboard_passes_selected_academic_period_to_kpis(client, monkeypatch):
    period = AcademicPeriod.objects.create(
        year_start=2025,
        year_end=2026,
        semester=2,
        start_date="2026-01-10",
        end_date="2026-05-20",
    )
    user = get_user_model().objects.create_user(
        username="period-admin@knust.edu.gh",
        email="period-admin@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    captured = {}

    def capture_reach(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("apps.reporting.views.kpi_reach", capture_reach)
    client.force_login(user)

    response = client.get(reverse("dashboard"), {"period": f"academic-{period.id}"})

    assert response.status_code == 200
    assert response.context["selected_period"] == f"academic-{period.id}"
    assert captured["since"].date() == dt.date(2026, 1, 10)
    assert captured["until"].date() == dt.date(2026, 5, 20)


@pytest.mark.django_db
def test_dashboard_scope_filters_apply_to_kpis_and_exports(client):
    user = get_user_model().objects.create_user(
        username="scope-filter-admin@knust.edu.gh",
        email="scope-filter-admin@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    programme = Programme.objects.create(name="Scope filter programme")
    organisation = Organisation.objects.create(
        name="Scope filter partner", type="company"
    )
    partner = Partner.objects.create(organisation=organisation, role="partner")
    ProgrammePartner.objects.create(programme=programme, partner=partner)
    session = Session.objects.create(
        programme=programme,
        title="Scoped session",
        start_at=dt.datetime(2026, 10, 1, 9, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 10, 1, 17, tzinfo=dt.timezone.utc),
        mode="in_person",
        status="closed",
    )
    SessionPartner.objects.create(session=session, partner=partner)
    staff = Person.objects.create(
        affiliation="staff", category="academic", full_name="Scoped Staff"
    )
    external = Person.objects.create(
        affiliation="external", category="community", full_name="Scoped External"
    )
    for person in (staff, external):
        Attendance.objects.create(
            session=session,
            person=person,
            method="manual",
            check_in_at=session.start_at,
            status="completed",
        )
    client.force_login(user)

    response = client.get(
        reverse("dashboard"),
        {"category": "academic", "affiliation": "staff", "partner": partner.id},
    )
    export = client.get(
        reverse("dashboard"),
        {"category": "academic", "affiliation": "staff", "partner": partner.id, "export": "csv"},
    )

    assert response.status_code == 200
    assert response.context["reach"] == 1
    assert response.context["workshops_held"] == 1
    assert response.context["selected_category"] == "academic"
    assert response.context["selected_affiliation"] == "staff"
    assert response.context["selected_partner"] == str(partner.id)
    assert export.status_code == 200
    assert b"Scope filters,Category,academic" in export.content
    assert b"Scope filters,Affiliation,staff" in export.content


@pytest.mark.django_db
def test_dashboard_renders_srs_chart_data(client, monkeypatch):
    user = get_user_model().objects.create_user(
        username="srs-charts-admin@knust.edu.gh",
        email="srs-charts-admin@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    monkeypatch.setattr(
        "apps.reporting.views.kpi_csat_trend",
        lambda **kwargs: [{"month": "Nov 2026", "mean": 4.2, "responses": 8}],
    )
    monkeypatch.setattr(
        "apps.reporting.views.kpi_progression_by_domain",
        lambda **kwargs: [{"domain": "AI", "mean_delta": 1.2, "people": 8}],
    )
    monkeypatch.setattr(
        "apps.reporting.views.kpi_coverage_heatmap",
        lambda **kwargs: [{"unit": "Science", "domain": "AI", "people": 8, "total": 10, "percent": 80}],
    )
    monkeypatch.setattr(
        "apps.reporting.views.kpi_external_reach",
        lambda **kwargs: [{"region": "Ashanti", "organisation_type": "school", "people": 8}],
    )
    monkeypatch.setattr(
        "apps.reporting.views.kpi_cascade_by_trainer",
        lambda **kwargs: [{"trainer": "Ama Trainer", "multiplier": 2.0, "direct": 4, "second_generation": 4}],
    )
    client.force_login(user)

    response = client.get(reverse("dashboard"))
    html = response.content.decode()

    assert response.status_code == 200
    for chart_id in (
        "pipelineChart",
        "progressionChart",
        "cascadeChart",
        "csatTrendChart",
        "externalReachChart",
    ):
        assert f'id="{chart_id}"' in html
    assert "Coverage heatmap" in html
    assert response.context["coverage_heatmap"][0]["percent"] == 80
