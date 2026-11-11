"""Library views - Competency Library governance and UI."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.permissions import is_super_admin
from apps.library.models import Competency, Domain, Framework, Level
from apps.programmes.models import ProgrammeCompetency


def _can_edit_library(user):
    """Check if user is a super admin or library curator."""
    if is_super_admin(user):
        return True
    try:
        roles = user.hub_account.roles.values_list("role", flat=True)
        return "library_curator" in roles
    except AttributeError:
        return False


@login_required
def library_dashboard(request):
    """Main Library view showing frameworks, domains, and competencies."""
    from django.db.models import Count, Prefetch

    if not _can_edit_library(request.user):
        messages.error(request, "You do not have permission to edit the library.")
        return redirect("hub_home")

    # All frameworks - clickable list
    frameworks = Framework.objects.all().order_by("-is_knust", "code")

    # Selected framework 
    framework_id = request.GET.get("framework")
    if framework_id:
        framework = get_object_or_404(Framework, id=framework_id)
    else:
        framework = frameworks.filter(is_knust=True).first() or frameworks.first()

    domains = []
    domain = None
    competencies = []
    levels = []

    if framework:
        domains = framework.domains.all().order_by("display_order")

        # Selected domain (default first)
        domain_id = request.GET.get("domain")
        if domain_id:
            domain = get_object_or_404(Domain, id=domain_id, framework=framework)
        elif domains:
            domain = domains.first()

        if domain:
            competencies = (
                domain.competencies.all()
                .annotate(usage_count=Count("programme_targets"))
                .order_by("code")
            )

        # Framework-level descriptors (5 levels)
        levels = framework.levels.all().order_by("ordinal")

    return render(
        request,
        "library/dashboard.html",
        {
            "frameworks": frameworks,
            "framework": framework,
            "domains": domains,
            "domain": domain,
            "competencies": competencies,
            "levels": levels,
        },
    )




@login_required
def competency_edit(request, competency_id):
    """Draft an edit for a competency (Draft → Publish)."""
    if not _can_edit_library(request.user):
        messages.error(request, "You do not have permission.")
        return redirect("hub_home")

    competency = get_object_or_404(Competency, id=competency_id)

    if request.method == "POST":
        # In a real draft/publish system, we'd save this to a 'draft' table first.
        # For simplicity in this MVP, we'll save directly but log the change.
        old_title = competency.title
        old_desc = competency.description
        
        competency.title = request.POST.get("title", competency.title)
        competency.description = request.POST.get("description", competency.description)
        competency.proficient_descriptor = request.POST.get("proficient_descriptor", competency.proficient_descriptor)
        competency.save()

        from apps.audit.services import log_event
        log_event(
            request=request,
            action="library_edit",
            entity="Competency",
            entity_id=competency.id,
            before={"title": old_title, "description": old_desc},
            after={"title": competency.title, "description": competency.description},
        )

        messages.success(request, f"Competency {competency.code} updated.")
        return redirect(f"/library/?domain={competency.domain_id}")

    # Calculate impact: how many programmes use this competency?
    programmes_using = ProgrammeCompetency.objects.filter(competency=competency).select_related("programme")

    return render(
        request,
        "library/competency_edit.html",
        {
            "competency": competency,
            "programmes_using": programmes_using,
        },
    )
    
@login_required
def competency_new(request):
    """Create a new competency via the admin (temporary)."""
    if not _can_edit_library(request.user):
        messages.error(request, "You do not have permission.")
        return redirect("hub_home")

    return redirect("/admin/library/competency/add/")    