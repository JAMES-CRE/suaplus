from django.http import JsonResponse
from django.db import connection

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from apps.accounts.permissions import is_super_admin
from apps.core.models import AcademicPeriod
from django.shortcuts import render

def health_check(request):
    """Health check endpoint for UITS monitoring."""
    try:
        # Check database connection
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False

    status = 200 if db_ok else 503
    return JsonResponse({
        "status": "ok" if db_ok else "error",
        "database": db_ok,
    }, status=status)
    
    
@login_required
def calendar_list(request):
    """List all academic periods."""
    if not is_super_admin(request.user):
        messages.error(request, "You do not have permission to manage the calendar.")
        return redirect("hub_home")

    periods = AcademicPeriod.objects.all().order_by("-year_start", "-semester")
    
    return render(
        request,
        "core/calendar_list.html",
        {"periods": periods},
    )


@login_required
def calendar_create(request):
    """Create a new academic period."""
    if not is_super_admin(request.user):
        return redirect("hub_home")

    if request.method == "POST":
        year_start = request.POST.get("year_start")
        year_end = request.POST.get("year_end")
        semester = request.POST.get("semester")
        start_date = request.POST.get("start_date")
        end_date = request.POST.get("end_date")

        # Basic validation
        try:
            period, created = AcademicPeriod.objects.get_or_create(
                year_start=int(year_start),
                year_end=int(year_end),
                semester=int(semester),
                defaults={
                    "start_date": start_date,
                    "end_date": end_date,
                }
            )
            if created:
                messages.success(request, f"Academic period {period} created.")
            else:
                messages.warning(request, "This academic period already exists.")
            return redirect("calendar_list")
        except ValueError:
            messages.error(request, "Invalid dates or numbers provided.")

    return render(request, "core/calendar_create.html")


@login_required
def calendar_edit(request, period_id):
    """Edit an existing academic period."""
    if not is_super_admin(request.user):
        messages.error(request, "You do not have permission to manage the calendar.")
        return redirect("hub_home")

    period = get_object_or_404(AcademicPeriod, id=period_id)

    if request.method == "POST":
        # Update the fields manually from the POST data
        period.year_start = request.POST.get("year_start")
        period.year_end = request.POST.get("year_end")
        period.semester = request.POST.get("semester")
        period.start_date = request.POST.get("start_date")
        period.end_date = request.POST.get("end_date")
        period.save()
        
        messages.success(request, f"Period {period.year_start}/{period.year_end} updated.")
        return redirect("calendar_list")

    return render(
        request,
        "core/calendar_edit.html",
        {"period": period},
    )




def landing_page(request):
    """The public landing page for Sua+."""
    return render(request, "public/index.html")