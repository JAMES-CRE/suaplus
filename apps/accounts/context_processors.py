"""Navigation mirrors the same capabilities enforced by server-side views."""
from apps.accounts.permissions import can


def navigation(request):
    if not hasattr(request, "_sua_navigation"):
        request._sua_navigation = {"sua_nav": {
            "sessions": can(request.user, "view_sessions"),
            "reports": can(request.user, "view_dashboard"),
            "reviews": can(request.user, "review_credentials"),
            "library": can(request.user, "manage_library"),
            "people": can(request.user, "view_people"),
            "scout": can(request.user, "nominate_trainers"),
            "memos": can(request.user, "export_reports"),
            "admin": can(request.user, "manage_users"),
            "programmes": can(request.user, "manage_programmes"),
            "records": can(request.user, "edit_records"),
            "identities": can(request.user, "edit_records"),
        }}
    from django.conf import settings
    request._sua_navigation["google_oauth_enabled"] = settings.GOOGLE_OAUTH_ENABLED
    match = getattr(request, "resolver_match", None)
    name = getattr(match, "url_name", "") or ""
    path = request.path
    request._sua_navigation["nav_current"] = {
        "overview": name in {"home", "hub_home"}, "passport": name.startswith("passport") or name in {"my_passport", "credential_submit", "credential_file"},
        "sessions": path.startswith(("/sessions/", "/import/")), "people": name == "person_list",
        "trainers": path.startswith(("/scout/", "/nominations/", "/recognition/")), "programmes": "/admin/programmes/" in path,
        "reviews": path.startswith(("/credentials/review/", "/credentials/internal/")), "identities": path.startswith("/identity-review/"),
        "records": "/admin/training/" in path, "reports": path.startswith(("/dashboard/", "/partners/")), "memos": path.startswith("/memos/"),
        "library": path.startswith("/library/"), "admin": path.startswith(("/admin/", "/admin-tools/", "/calendar/")),
        "assessment": path.startswith("/sas/"), "recovery": name == "resend_link",
    }
    from apps.accounts.permissions import get_user_scopes, is_super_admin
    roles = {scope.role for scope in get_user_scopes(request.user)}
    role_labels = [("programme_owner", "Programme owner"), ("focal_person", "Focal person"), ("facilitator", "Trainer"), ("associate_trainer", "Associate trainer"), ("executive_viewer", "Executive reporting"), ("library_curator", "Library curator"), ("partner_coordinator", "Partner coordinator")]
    request._sua_navigation["workspace_role"] = "Administrator" if is_super_admin(request.user) else next((label for role, label in role_labels if role in roles), "Learning workspace")
    return request._sua_navigation
