"""Immutable Library version snapshots and evidence version labels."""

from django.db import transaction
from django.db.models import Max

from apps.library.models import (
    EvidenceRule,
    Framework,
    LibraryVersion,
)


def _field_snapshot(instance):
    """Return JSON-safe values for an object's concrete model fields."""
    return {
        field.name: field.value_to_string(instance)
        for field in instance._meta.concrete_fields
        if not field.primary_key
    }


def _framework_snapshot(framework):
    """Capture a framework and the library records interpreted under it."""
    from apps.library.models import Level

    domains = []
    for domain in framework.domains.order_by("display_order", "code"):
        competencies = []
        for competency in domain.competencies.order_by("code"):
            competencies.append(
                {
                    **_field_snapshot(competency),
                    "crosswalks": [
                        {
                            "external_competency_id": row.external_competency_id,
                            "external_framework": row.external_competency.framework.code,
                            "external_level_id": row.external_level_id,
                            "note": row.note,
                        }
                        for row in competency.crosswalks_from.select_related(
                            "external_competency__framework", "external_level"
                        ).all()
                    ],
                    "evidence_rules": [
                        _field_snapshot(rule)
                        for rule in EvidenceRule.objects.filter(
                            competency=competency
                        ).order_by("evidence_type", "id")
                    ],
                }
            )
        domains.append(
            {
                **_field_snapshot(domain),
                "competencies": competencies,
            }
        )

    return {
        "framework": _field_snapshot(framework),
        "levels": [
            _field_snapshot(level)
            for level in Level.objects.filter(framework=framework).order_by("ordinal")
        ],
        "domains": domains,
        "global_evidence_rules": [
            _field_snapshot(rule)
            for rule in EvidenceRule.objects.filter(competency__isnull=True).order_by(
                "evidence_type", "id"
            )
        ],
    }


def _related_frameworks(item_type, item):
    if item_type in {"framework", "framework_snapshot"}:
        return [item]
    if item_type == "domain":
        return [item.framework]
    if item_type == "level":
        return [item.framework]
    if item_type == "competency":
        return [item.framework]
    if item_type == "crosswalk":
        return [item.knust_competency.framework]
    if item_type == "evidence_rule":
        if item.competency_id:
            return [item.competency.framework]
        return list(Framework.objects.all())
    return []


def record_library_publication(
    item_type, item, change_reason, actor, approved_by=None
):
    """Store an immutable item version and new full snapshots of affected frameworks."""
    if item_type == "framework_snapshot":
        raise ValueError("Framework snapshots are generated automatically.")

    frameworks = _related_frameworks(item_type, item)
    item_id = item.pk
    previous_version = LibraryVersion.objects.filter(
        item_type=item_type, item_id=item_id
    ).aggregate(value=Max("version_number"))["value"] or 0

    with transaction.atomic():
        for framework in frameworks:
            has_snapshot = LibraryVersion.objects.filter(
                item_type="framework_snapshot",
                item_id=framework.pk,
            ).exists()
            framework.revision = max(1, framework.revision + 1 if has_snapshot else framework.revision)
            framework.save(update_fields=["revision", "updated_at"])

        framework_for_item = frameworks[0] if len(frameworks) == 1 else None
        LibraryVersion.objects.create(
            framework=framework_for_item,
            item_type=item_type,
            item_id=item_id,
            version_number=previous_version + 1,
            framework_revision=(framework_for_item.revision if framework_for_item else 1),
            snapshot={"model": item._meta.label, "fields": _field_snapshot(item)},
            change_reason=change_reason,
            created_by=actor,
            approved_by=approved_by,
        )

        for framework in frameworks:
            LibraryVersion.objects.create(
                framework=framework,
                item_type="framework_snapshot",
                item_id=framework.pk,
                version_number=framework.revision,
                framework_revision=framework.revision,
                snapshot=_framework_snapshot(framework),
                change_reason=change_reason,
                created_by=actor,
                approved_by=approved_by,
            )


def ensure_framework_snapshot(framework):
    """Create a baseline snapshot when legacy seed data has no version history yet."""
    existing = LibraryVersion.objects.filter(
        item_type="framework_snapshot",
        item_id=framework.pk,
        version_number=framework.revision,
    ).first()
    if existing:
        return existing
    return LibraryVersion.objects.create(
        framework=framework,
        item_type="framework_snapshot",
        item_id=framework.pk,
        version_number=framework.revision,
        framework_revision=framework.revision,
        snapshot=_framework_snapshot(framework),
        change_reason="Initial framework snapshot",
    )


def framework_version_label(framework):
    ensure_framework_snapshot(framework)
    return f"{framework.code}:{framework.revision}"
