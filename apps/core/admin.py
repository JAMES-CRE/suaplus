from django.contrib import admin

from .models import AcademicPeriod


@admin.register(AcademicPeriod)
class AcademicPeriodAdmin(admin.ModelAdmin):
    list_display = ("year_start", "year_end", "semester", "start_date", "end_date")
    list_filter = ("semester",)