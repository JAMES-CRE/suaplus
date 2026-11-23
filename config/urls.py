from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.accounts import views as accounts_views
from apps.audit import views as audit_views
from apps.core import views as core_views
from apps.core.views import health_check, landing_page
from apps.imports import views as imports_views
from apps.library import views as library_views
from apps.passport import views as passport_views
from apps.passport.internal_views import internal_assessment, badge_public_key
from apps.programmes import views as programmes_views
from apps.registry import views as registry_views
from apps.reporting import views as reporting_views
from apps.sessions import views as sessions_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),

    
    path("login/", RedirectView.as_view(url="/accounts/login/", permanent=False), name="login"),

    path("accounts/cancel-code/", accounts_views.cancel_login_code, name="cancel_login_code"),
    path("identity-review/", registry_views.identity_review_list, name="identity_review_list"),
    path("identity-review/<int:conflict_id>/", registry_views.identity_review_detail, name="identity_review_detail"),
    path("people/", accounts_views.person_list, name="person_list"),

    # Public check-in (no login)
    path("checkin/<str:code>/", sessions_views.checkin, name="checkin"),
    path("checkin/<str:code>/done/", sessions_views.checkin_done, name="checkin_done"),
    path("scout/", programmes_views.scout_list, name="scout_list"),
    path("checkout/<str:code>/<int:person_id>/", sessions_views.checkout, name="checkout"),
    path("scout/nominate/<int:tier_status_id>/", programmes_views.nominate, name="nominate"),
    path("nominations/", programmes_views.nomination_list, name="nomination_list"),
    path("nominations/<int:nomination_id>/", programmes_views.nomination_detail, name="nomination_detail"),
    path("p/<str:token>/goals/", passport_views.passport_goals, name="passport_goals"),
    path("p/<str:token>/", passport_views.passport_view, name="passport"),
    path("sessions/", sessions_views.session_list, name="session_list"),
    path("sessions/create/", sessions_views.session_create, name="session_create"),
    path("sessions/<int:session_id>/edit/", sessions_views.session_edit, name="session_edit"),
    path("sessions/<int:session_id>/attendance/<int:attendance_id>/confirm/", sessions_views.attendance_confirm, name="attendance_confirm"),
    path("sessions/<int:session_id>/console/", sessions_views.session_console, name="session_console"),
    path("sessions/<int:session_id>/close/", sessions_views.session_close, name="session_close"),
    path("sessions/<int:session_id>/qr.json", sessions_views.session_qr_json, name="session_qr_json"),
    path("dashboard/", reporting_views.dashboard_overview, name="dashboard"),
    path("dashboard/<str:kpi>/", reporting_views.drilldown, name="drilldown"),
    path("memos/", reporting_views.memo_list, name="memo_list"),
    path("memos/generate/", reporting_views.memo_generate, name="memo_generate"),
    path("memos/<int:memo_id>/", reporting_views.memo_detail, name="memo_detail"),
    path("credentials/<int:credential_id>/file/", passport_views.credential_file, name="credential_file"),
    path("credentials/internal/<int:attendance_id>/", internal_assessment, name="internal_assessment"),
    path("credentials/submit/", passport_views.credential_submit, name="credential_submit"),
    path("credentials/review/", passport_views.credential_review_queue, name="credential_review_queue"),
    path("credentials/review/<int:credential_id>/", passport_views.credential_review_detail, name="credential_review_detail"),
    path("sas/", passport_views.sas_start, name="sas_start"),
    path("sas/confirm/<str:token>/", passport_views.sas_confirm, name="sas_confirm"),
    path("import/upload/", imports_views.import_upload, name="import_upload"),
    path("import/preview/", imports_views.import_preview, name="import_preview"),
    path("import/history/<int:session_id>/", imports_views.import_history, name="import_history"),
    path("import/batch/<int:batch_id>/", imports_views.import_detail, name="import_detail"),
    path("import/batch/<int:batch_id>/undo/", imports_views.import_undo, name="import_undo"),
    path("import/commit/", imports_views.import_commit, name="import_commit"),

    
    path("admin-tools/dsr/", audit_views.dsr_dashboard, name="dsr_dashboard"),
    path("admin-tools/dsr/export/<int:person_id>/", audit_views.dsr_export, name="dsr_export"),
    path("admin-tools/dsr/erase/<int:person_id>/", audit_views.dsr_erase, name="dsr_erase"),
    path("admin-tools/audit-log/", audit_views.audit_log_list, name="audit_log"),
    path("badges/public-key/", badge_public_key, name="badge_public_key"),
    path("badges/verify/<str:badge_jwt>/", passport_views.verify_badge_view, name="badge_verify"),
    path("sessions/<int:session_id>/live.json", sessions_views.session_live_stats, name="session_live_stats"),
    path("hub/", accounts_views.hub_home, name="hub_home"),
    path("resend/", passport_views.resend_link, name="resend_link"),
    path("p/<str:token>/report/", passport_views.report_error, name="report_error"),
    path("account/totp/setup/", accounts_views.totp_setup, name="totp_setup"),
    path("account/totp/setup/qr/", accounts_views.totp_setup_qr, name="totp_setup_qr"),
    path("account/profile/", accounts_views.profile_completion, name="profile_completion"),
    path("followup/<str:token>/", sessions_views.followup_form, name="followup_form"),
    path("p/<str:token>/transcript/", passport_views.passport_transcript, name="passport_transcript"),
    path("p/<str:token>/transcript.pdf", passport_views.passport_transcript_pdf, name="passport_transcript_pdf"),
    path("partners/", programmes_views.partner_report_list, name="partner_report_list"),
    path("partners/<int:org_id>/", programmes_views.partner_report, name="partner_report"),
    path("recognition/", programmes_views.recognition_list, name="recognition_list"),
    path("live/", core_views.liveness, name="liveness"),
    path("ready/", core_views.readiness, name="readiness"),
    path("health/", health_check, name="health_check"),
    path("library/", library_views.library_dashboard, name="library_dashboard"),
    path("library/transfer/", library_views.library_transfer, name="library_transfer"),
    path("library/where-used/<str:item_type>/<int:item_id>/", library_views.library_where_used, name="library_where_used"),
    path("library/competency/<int:competency_id>/edit/", library_views.competency_edit, name="competency_edit"),
    path("library/competency/draft/<int:draft_id>/", library_views.competency_draft_detail, name="competency_draft_detail"),
    path("library/competency/new/", library_views.competency_new, name="competency_new"),
    path("calendar/", core_views.calendar_list, name="calendar_list"),
    path("calendar/create/", core_views.calendar_create, name="calendar_create"),
    path("calendar/<int:period_id>/edit/", core_views.calendar_edit, name="calendar_edit"),
    path("sessions/<int:session_id>/qr-fragment/", sessions_views.session_qr_fragment, name="session_qr_fragment"),
    path("sessions/<int:session_id>/roster-fragment/", sessions_views.session_roster_fragment, name="session_roster_fragment"),
    
    path("", landing_page, name="home"),
    path("p/<str:token>/profile/edit/", passport_views.passport_profile_edit, name="passport_profile_edit"),
    path("p/<str:token>/profile/", passport_views.passport_profile, name="passport_profile"),
    path("sessions/<int:session_id>/checkout/", sessions_views.session_checkout_console, name="session_checkout_console"),
    path("checkout/identify/<str:code>/", sessions_views.checkout_identify, name="checkout_identify"),
    path("my-passport/", passport_views.my_passport, name="my_passport"),
    
]


# Customize Django Admin Branding
admin.site.site_header = "Sua+ Administration"
admin.site.site_title = "Sua+ Admin"
admin.site.index_title = "Welcome to the Sua+ Back Office"
