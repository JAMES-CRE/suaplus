from django.test import TestCase

# Create your tests here.
import pytest
from datetime import date
from django.core.files.uploadedfile import SimpleUploadedFile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import RoleAssignment, UserAccount
from apps.library.models import (
    Competency,
    CompetencyDraft,
    CertificationCatalogue,
    CertificationCompetency,
    Crosswalk,
    CrosswalkDraft,
    Domain,
    DomainDraft,
    EvidenceRule,
    EvidenceRuleDraft,
    Framework,
    FrameworkDraft,
    LibraryVersion,
    Level,
    LevelDraft,
    SurveyTemplate,
)
from apps.library.versioning import framework_version_label


def _curator(username, is_staff=False):
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@knust.edu.gh",
        password="test-password",
        is_staff=is_staff,
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account,
        role="library_curator",
        scope_type="all",
    )
    return user


@pytest.fixture
def competency(db):
    framework = Framework.objects.create(code="KNUST", name="KNUST Framework", is_knust=True)
    domain = Domain.objects.create(framework=framework, code="AI", name="AI Competency")
    return Competency.objects.create(
        framework=framework,
        domain=domain,
        code="AI-01",
        title="AI foundations",
        description="Current description",
        proficient_descriptor="Current level 3 descriptor",
    )


@pytest.mark.django_db
def test_competency_edits_remain_drafts_until_explicit_publish(client, competency):
    curator = _curator("curator-one")
    client.force_login(curator)

    response = client.post(
        reverse("competency_edit", args=[competency.id]),
        {
            "title": "Updated AI foundations",
            "description": "Updated description",
            "proficient_descriptor": competency.proficient_descriptor,
            "change_reason": "Clarify the learning outcome.",
        },
    )

    draft = CompetencyDraft.objects.get()
    competency.refresh_from_db()
    assert response.status_code == 302
    assert competency.title == "AI foundations"
    assert draft.status == "draft"

    response = client.post(reverse("competency_draft_detail", args=[draft.id]))

    competency.refresh_from_db()
    draft.refresh_from_db()
    assert response.status_code == 200
    assert competency.title == "Updated AI foundations"
    assert draft.status == "published"


@pytest.mark.django_db
def test_level_descriptor_change_needs_a_second_curator(client, competency):
    author = _curator("curator-author")
    second_curator = _curator("curator-approver")
    client.force_login(author)

    client.post(
        reverse("competency_edit", args=[competency.id]),
        {
            "title": competency.title,
            "description": competency.description,
            "proficient_descriptor": "Revised level 3 descriptor",
            "change_reason": "Align descriptor with the approved scale.",
        },
    )
    draft = CompetencyDraft.objects.get()
    url = reverse("competency_draft_detail", args=[draft.id])

    client.post(url)
    draft.refresh_from_db()
    competency.refresh_from_db()
    assert draft.status == "awaiting_approval"
    assert competency.proficient_descriptor == "Current level 3 descriptor"

    client.force_login(second_curator)
    response = client.post(url)

    draft.refresh_from_db()
    competency.refresh_from_db()
    assert response.status_code == 200
    assert draft.status == "published"
    assert draft.approved_by == second_curator
    assert competency.proficient_descriptor == "Revised level 3 descriptor"


@pytest.mark.django_db
def test_crosswalk_draft_requires_second_curator_before_publish(client):
    knust = Framework.objects.create(code="KNUST", name="KNUST", is_knust=True)
    external = Framework.objects.create(code="EXT", name="External")
    knust_domain = Domain.objects.create(framework=knust, code="AI", name="AI")
    ext_domain = Domain.objects.create(framework=external, code="AI", name="AI")
    source = Competency.objects.create(
        framework=knust, domain=knust_domain, code="AI-01", title="AI foundations"
    )
    target = Competency.objects.create(
        framework=external, domain=ext_domain, code="EXT-01", title="AI basics"
    )
    external_level = Level.objects.create(
        framework=external, ordinal=1, label="Foundational"
    )
    author = _curator("crosswalk-author", is_staff=True)
    approver = _curator("crosswalk-approver", is_staff=True)
    draft = CrosswalkDraft.objects.create(
        knust_competency=source,
        external_competency=target,
        external_level=external_level,
        note="Mapped to the external foundational outcome.",
        change_reason="Align the starter framework crosswalk.",
        created_by=author,
    )
    url = reverse("admin:library_crosswalkdraft_changelist")
    action = {
        "_selected_action": str(draft.id),
        "select_across": "0",
        "index": "0",
    }

    client.force_login(author)
    client.post(url, {**action, "action": "request_second_approval"})
    draft.refresh_from_db()
    assert draft.status == "awaiting_approval"

    client.post(url, {**action, "action": "approve_and_publish"})
    draft.refresh_from_db()
    assert draft.status == "awaiting_approval"
    assert not Crosswalk.objects.filter(knust_competency=source).exists()

    client.force_login(approver)
    response = client.post(url, {**action, "action": "approve_and_publish"})

    draft.refresh_from_db()
    crosswalk = Crosswalk.objects.get(knust_competency=source)
    assert response.status_code == 302
    assert draft.status == "published"
    assert draft.approved_by == approver
    assert crosswalk.external_competency == target


@pytest.mark.django_db
def test_evidence_rule_draft_requires_second_curator_before_publish(client):
    framework = Framework.objects.create(code="KNUST", name="KNUST", is_knust=True)
    domain = Domain.objects.create(framework=framework, code="AI", name="AI")
    competency = Competency.objects.create(
        framework=framework, domain=domain, code="AI-02", title="Prompting"
    )
    author = _curator("rule-author", is_staff=True)
    approver = _curator("rule-approver", is_staff=True)
    draft = EvidenceRuleDraft.objects.create(
        evidence_type="external",
        competency=competency,
        max_level=3,
        description="External certification supports this level.",
        change_reason="Apply the approved certification evidence cap.",
        created_by=author,
    )
    url = reverse("admin:library_evidenceruledraft_changelist")
    action = {
        "_selected_action": str(draft.id),
        "select_across": "0",
        "index": "0",
    }

    client.force_login(author)
    client.post(url, {**action, "action": "request_second_approval"})
    client.post(url, {**action, "action": "approve_and_publish"})
    draft.refresh_from_db()
    assert draft.status == "awaiting_approval"
    assert not EvidenceRule.objects.exists()

    client.force_login(approver)
    response = client.post(url, {**action, "action": "approve_and_publish"})

    draft.refresh_from_db()
    rule = EvidenceRule.objects.get(competency=competency, evidence_type="external")
    assert response.status_code == 302
    assert draft.status == "published"
    assert draft.approved_by == approver
    assert rule.max_level == 3
    assert rule.status == "active"


@pytest.mark.django_db
def test_domain_changes_and_retirement_apply_only_when_draft_is_published(client):
    framework = Framework.objects.create(code="KNUST", name="KNUST", is_knust=True)
    domain = Domain.objects.create(
        framework=framework,
        code="AI",
        name="AI Competency",
        status="active",
    )
    curator = _curator("domain-curator", is_staff=True)
    draft = DomainDraft.objects.create(
        domain=domain,
        framework=framework,
        code="AI",
        name="Responsible AI",
        description="Updated domain description",
        colour="#002147",
        display_order=2,
        proposed_status="retired",
        change_reason="Retire this domain after framework restructuring.",
        created_by=curator,
    )
    client.force_login(curator)
    response = client.post(
        reverse("admin:library_domaindraft_changelist"),
        {
            "action": "publish_drafts",
            "_selected_action": str(draft.id),
            "select_across": "0",
            "index": "0",
        },
    )

    domain.refresh_from_db()
    draft.refresh_from_db()
    assert response.status_code == 302
    assert domain.name == "Responsible AI"
    assert domain.status == "retired"
    assert draft.status == "published"


@pytest.mark.django_db
def test_framework_changes_apply_only_when_draft_is_published(client):
    framework = Framework.objects.create(
        code="EXT",
        name="Current Framework",
        publisher="Old Publisher",
        status="active",
    )
    curator = _curator("framework-curator", is_staff=True)
    draft = FrameworkDraft.objects.create(
        framework=framework,
        code="EXT",
        name="Revised Framework",
        publisher="New Publisher",
        version="2.0",
        source="manual",
        is_knust=False,
        proposed_status="published",
        change_reason="Publish the approved framework revision.",
        created_by=curator,
    )
    client.force_login(curator)

    framework.refresh_from_db()
    assert framework.name == "Current Framework"
    assert draft.status == "draft"

    response = client.post(
        reverse("admin:library_frameworkdraft_changelist"),
        {
            "action": "publish_drafts",
            "_selected_action": str(draft.id),
            "select_across": "0",
            "index": "0",
        },
    )

    framework.refresh_from_db()
    draft.refresh_from_db()
    assert response.status_code == 302
    assert framework.name == "Revised Framework"
    assert framework.version == "2.0"
    assert framework.status == "published"
    assert framework.published_at is not None
    assert draft.status == "published"
    item_version = LibraryVersion.objects.get(item_type="framework", item_id=framework.id)
    framework_snapshot = LibraryVersion.objects.get(
        item_type="framework_snapshot", item_id=framework.id
    )
    assert item_version.change_reason == "Publish the approved framework revision."
    assert item_version.snapshot["fields"]["name"] == "Revised Framework"
    assert framework_snapshot.snapshot["framework"]["name"] == "Revised Framework"
    assert framework_version_label(framework) == f"EXT:{framework.revision}"


@pytest.mark.django_db
def test_library_versions_cannot_be_changed_or_deleted(competency):
    curator = _curator("version-curator")
    version = LibraryVersion.objects.create(
        framework=competency.framework,
        item_type="competency",
        item_id=competency.id,
        version_number=1,
        framework_revision=1,
        snapshot={"title": competency.title},
        change_reason="Initial snapshot",
        created_by=curator,
    )

    version.snapshot = {"title": "Changed"}
    with pytest.raises(ValidationError):
        version.save()
    with pytest.raises(ValidationError):
        version.delete()


@pytest.mark.django_db
def test_level_descriptor_requires_second_curator_and_is_versioned(client):
    framework = Framework.objects.create(code="KNUST", name="KNUST", is_knust=True)
    level = Level.objects.create(
        framework=framework,
        ordinal=3,
        label="Proficient",
        descriptor="Current descriptor",
    )
    author = _curator("level-author", is_staff=True)
    approver = _curator("level-approver", is_staff=True)
    draft = LevelDraft.objects.create(
        level=level,
        framework=framework,
        ordinal=3,
        label="Proficient",
        descriptor="Approved revised descriptor",
        change_reason="Align the level descriptor to the approved framework.",
        created_by=author,
    )
    url = reverse("admin:library_leveldraft_changelist")
    action = {
        "_selected_action": str(draft.id),
        "select_across": "0",
        "index": "0",
    }

    client.force_login(author)
    client.post(url, {**action, "action": "request_second_approval"})
    client.post(url, {**action, "action": "approve_and_publish"})
    level.refresh_from_db()
    draft.refresh_from_db()
    assert draft.status == "awaiting_approval"
    assert level.descriptor == "Current descriptor"

    client.force_login(approver)
    response = client.post(url, {**action, "action": "approve_and_publish"})

    level.refresh_from_db()
    draft.refresh_from_db()
    assert response.status_code == 302
    assert draft.status == "published"
    assert draft.approved_by == approver
    assert level.descriptor == "Approved revised descriptor"
    version = LibraryVersion.objects.get(item_type="level", item_id=level.id)
    assert version.snapshot["fields"]["descriptor"] == "Approved revised descriptor"
    assert version.approved_by == approver


@pytest.mark.django_db
def test_library_search_finds_competencies_across_frameworks(client, competency):
    external_framework = Framework.objects.create(code="EXT", name="External Standard")
    external_domain = Domain.objects.create(
        framework=external_framework, code="DIG", name="Digital Skills"
    )
    external_competency = Competency.objects.create(
        framework=external_framework,
        domain=external_domain,
        code="EXT-22",
        title="Evaluating information",
        description="Search and assess information sources.",
    )
    curator = _curator("search-curator")
    client.force_login(curator)

    response = client.get(reverse("library_dashboard"), {"q": "EXT-22"})

    assert response.status_code == 200
    assert external_competency.title.encode() in response.content
    assert competency.title.encode() not in response.content
    assert b"Search the whole Library" in response.content


@pytest.mark.django_db
def test_library_search_filters_by_framework_domain_and_status(client):
    framework = Framework.objects.create(code="KNUST", name="KNUST", is_knust=True)
    domain = Domain.objects.create(framework=framework, code="AI", name="AI")
    active = Competency.objects.create(
        framework=framework, domain=domain, code="AI-01", title="Active AI"
    )
    retired = Competency.objects.create(
        framework=framework,
        domain=domain,
        code="AI-02",
        title="Retired AI",
        status="retired",
    )
    curator = _curator("filter-curator")
    client.force_login(curator)

    response = client.get(
        reverse("library_dashboard"),
        {
            "library_framework": framework.id,
            "library_domain": domain.id,
            "status": "retired",
        },
    )

    assert response.status_code == 200
    assert retired.title.encode() in response.content
    assert active.title.encode() not in response.content


@pytest.mark.django_db
def test_library_where_used_shows_dependencies_without_participant_names(client, competency):
    from apps.passport.models import Evidence
    from apps.registry.models import Person

    level = Level.objects.create(
        framework=competency.framework,
        ordinal=1,
        label="Aware",
        descriptor="Recognises the topic.",
    )
    participant = Person.objects.create(
        affiliation="staff", full_name="Private Participant Name"
    )
    Evidence.objects.create(
        person=participant,
        competency=competency,
        level=level,
        type="assessor",
    )
    curator = _curator("where-used-curator")
    client.force_login(curator)

    response = client.get(
        reverse("library_where_used", args=["competency", competency.id])
    )

    assert response.status_code == 200
    assert competency.code.encode() in response.content
    assert b"Evidence records" in response.content
    assert b"1 record" in response.content
    assert b"Private Participant Name" not in response.content


@pytest.mark.django_db
def test_library_where_used_supports_framework_and_domain(client, competency):
    curator = _curator("where-used-scope-curator")
    client.force_login(curator)

    framework_response = client.get(
        reverse("library_where_used", args=["framework", competency.framework_id])
    )
    domain_response = client.get(
        reverse("library_where_used", args=["domain", competency.domain_id])
    )

    assert framework_response.status_code == 200
    assert domain_response.status_code == 200
    assert competency.code.encode() in framework_response.content
    assert competency.code.encode() in domain_response.content


@pytest.mark.django_db
def test_library_where_used_supports_certification_and_survey_items(client, competency):
    certification = CertificationCatalogue.objects.create(
        issuer="Example Issuer", title="Example Certificate"
    )
    CertificationCompetency.objects.create(
        certification=certification, competency=competency, max_level=3
    )
    survey = SurveyTemplate.objects.create(
        name="Workshop Exit Survey", type="exit", definition={}
    )
    curator = _curator("catalogue-where-used-curator")
    client.force_login(curator)

    certification_response = client.get(
        reverse("library_where_used", args=["certification", certification.id])
    )
    survey_response = client.get(
        reverse("library_where_used", args=["survey", survey.id])
    )

    assert certification_response.status_code == 200
    assert competency.code.encode() in certification_response.content
    assert survey_response.status_code == 200
    assert b"Session survey responses" in survey_response.content


@pytest.mark.django_db
def test_new_competency_with_descriptor_waits_for_second_curator(client):
    framework = Framework.objects.create(code="KNUST", name="KNUST", is_knust=True)
    domain = Domain.objects.create(framework=framework, code="AI", name="AI")
    author = _curator("competency-author")
    approver = _curator("competency-approver")
    client.force_login(author)

    response = client.post(
        reverse("competency_new"),
        {
            "domain": domain.id,
            "code": "AI-01",
            "title": "AI foundations",
            "description": "Learn core AI concepts.",
            "proficient_descriptor": "Uses AI tools appropriately.",
            "change_reason": "Add the approved starter competency.",
        },
    )

    draft = CompetencyDraft.objects.get()
    assert response.status_code == 302
    assert not Competency.objects.filter(framework=framework, code="AI-01").exists()

    detail_url = reverse("competency_draft_detail", args=[draft.id])
    client.post(detail_url)
    draft.refresh_from_db()
    assert draft.status == "awaiting_approval"
    assert not Competency.objects.filter(framework=framework, code="AI-01").exists()

    client.force_login(approver)
    response = client.post(detail_url)

    draft.refresh_from_db()
    competency = Competency.objects.get(framework=framework, code="AI-01")
    assert response.status_code == 200
    assert draft.status == "published"
    assert draft.approved_by == approver
    assert competency.proficient_descriptor == "Uses AI tools appropriately."


@pytest.mark.django_db
def test_competency_retirement_is_applied_only_when_draft_is_published(client, competency):
    replacement = Competency.objects.create(
        framework=competency.framework,
        domain=competency.domain,
        code="AI-02",
        title="AI foundations and practice",
    )
    curator = _curator("competency-retirer")
    client.force_login(curator)
    response = client.post(
        reverse("competency_edit", args=[competency.id]),
        {
            "title": competency.title,
            "description": competency.description,
            "proficient_descriptor": competency.proficient_descriptor,
            "proposed_status": "retired",
            "proposed_replaced_by": replacement.id,
            "change_reason": "This competency has been replaced.",
        },
    )
    draft = CompetencyDraft.objects.get()
    competency.refresh_from_db()
    assert response.status_code == 302
    assert competency.status == "active"

    response = client.post(reverse("competency_draft_detail", args=[draft.id]))
    competency.refresh_from_db()
    assert response.status_code == 200
    assert competency.status == "retired"
    assert competency.replaced_by == replacement


@pytest.mark.django_db
def test_competency_cannot_be_retired_without_a_replacement(client, competency):
    curator = _curator("competency-retirement-check")
    client.force_login(curator)

    response = client.post(
        reverse("competency_edit", args=[competency.id]),
        {
            "title": competency.title,
            "description": competency.description,
            "proficient_descriptor": competency.proficient_descriptor,
            "proposed_status": "retired",
            "change_reason": "Retire without replacement.",
        },
    )

    assert response.status_code == 302
    assert not CompetencyDraft.objects.exists()
    competency.refresh_from_db()
    assert competency.status == "active"


@pytest.mark.django_db
def test_library_transfer_exports_csv_and_workbook(client):
    curator = _curator("library-transfer-export")
    client.force_login(curator)

    csv_response = client.get(reverse("library_transfer"), {"export": "csv", "entity": "competencies"})
    xlsx_response = client.get(reverse("library_transfer"), {"export": "xlsx"})

    assert csv_response.status_code == 200
    assert b"framework_code,domain_code,code,title" in csv_response.content
    assert "suaplus-library.xlsx" in xlsx_response["Content-Disposition"]
    assert xlsx_response["Content-Type"].startswith("application/vnd.openxmlformats-officedocument")


@pytest.mark.django_db
def test_library_transfer_import_creates_unpublished_competency_draft(client, competency):
    curator = _curator("library-transfer-import")
    client.force_login(curator)
    csv_content = (
        "framework_code,domain_code,code,title,description,proficient_descriptor,status,replaced_by_code\n"
        f"{competency.framework.code},{competency.domain.code},AI-IMPORT,Imported competency,,,active,\n"
    )
    upload = SimpleUploadedFile("competencies.csv", csv_content.encode(), content_type="text/csv")

    response = client.post(
        reverse("library_transfer"),
        {"file": upload, "entity": "competencies", "change_reason": "Import approved catalogue rows."},
    )

    draft = CompetencyDraft.objects.get(code="AI-IMPORT")
    assert response.status_code == 302
    assert draft.status == "draft"
    assert not Competency.objects.filter(framework=competency.framework, code="AI-IMPORT").exists()


@pytest.mark.django_db
def test_library_transfer_rejects_bad_headers_without_creating_drafts(client, competency):
    curator = _curator("library-transfer-invalid")
    client.force_login(curator)
    upload = SimpleUploadedFile("competencies.csv", b"code,title\nAI-IMPORT,Imported\n", content_type="text/csv")

    response = client.post(
        reverse("library_transfer"),
        {"file": upload, "entity": "competencies", "change_reason": "Test invalid data."},
    )

    assert response.status_code == 200
    assert b"CSV headers must match" in response.content
    assert not CompetencyDraft.objects.filter(code="AI-IMPORT").exists()
