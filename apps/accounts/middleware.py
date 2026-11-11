from django.shortcuts import redirect
from django_otp import user_has_device


class SuperAdminMFAMiddleware:
    """Force Super Admins to set up TOTP before accessing Hub pages."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.path.startswith("/hub/"):
            from apps.accounts.permissions import is_super_admin
            if is_super_admin(request.user) and not user_has_device(request.user):
                return redirect("/account/totp/setup/")
        return self.get_response(request)