from django.contrib import admin
from django.contrib import messages

from .models import (
    Consent,
    Organisation,
    OrgUnit,
    PassportToken,
    Person,
    UnitHeadcount,
)
from apps.audit.services import log_event


@admin.action(description="Merge selected person into first selected")
def merge_people(modeladmin, request, queryset):
    """Merge all selected people into the first one.

    Reassigns attendances, evidence and credentials to the target, then
    marks the other records as merged (reversible via merged_into).
    """
    if queryset.count() < 2:
        messages.error(
            request,
            "Select at least two people - the first is the target.",
        )
        return

    target = queryset.first()
    others = queryset.exclude(id=target.id)
    merged = 0

    for other in others:
        other.attendances.update(person=target)
        other.evidence.update(person=target)
        other.credentials.update(person=target)

        other.merged_into = target
        other.save(update_fields=["merged_into"])

        log_event(
            request,
            "person_merge",
            "Person",
            other.id,
            before={"merged_into": None},
            after={"merged_into": target.id},
        )
        merged += 1

    messages.success(
        request,
        f"Merged {merged} record{'s' if merged != 1 else ''} into {target.full_name}.",
    )


@admin.register(OrgUnit)
class OrgUnitAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "parent", "active")
    list_filter = ("type", "active")
    search_fields = ("name", "short_name")


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "region", "district")
    list_filter = ("type", "region")
    search_fields = ("name",)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("full_name", "affiliation", "email", "unit", "age_band")
    list_filter = ("affiliation", "category", "age_band")
    search_fields = ("full_name", "email", "staff_or_student_no")
    actions = [merge_people]

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Log every time an admin opens a person's record."""
        person = self.model.objects.filter(id=object_id).first()
        if person:
            log_event(
                request=request,
                action="view_person",
                entity="Person",
                entity_id=person.id,
                after={"name": person.full_name},
            )
        return super().change_view(request, object_id, form_url, extra_context)


@admin.register(Consent)
class ConsentAdmin(admin.ModelAdmin):
    list_display = ("person", "type", "given_at", "notice_version")
    list_filter = ("type",)


@admin.register(PassportToken)
class PassportTokenAdmin(admin.ModelAdmin):
    list_display = ("person", "created_at", "revoked_at", "last_used_at")


@admin.register(UnitHeadcount)
class UnitHeadcountAdmin(admin.ModelAdmin):
    list_display = ("unit", "category", "period", "headcount")
    list_filter = ("category", "period", "unit__type")
    search_fields = ("unit__name",)