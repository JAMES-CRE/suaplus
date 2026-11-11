from django.shortcuts import redirect


class ProfileCompletionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Exempt paths (accounts, admin, static, etc.)
        exempt = ("/accounts/", "/account/", "/admin/", "/static/", "/media/")
        
        if request.user.is_authenticated and not any(request.path.startswith(p) for p in exempt):
            try:
                account = request.user.hub_account
            except Exception:
                account = None

            if account and account.person:
                p = account.person
                # Check if profile is incomplete
                if not p.title or not p.category or (not p.unit_id and not p.unit_other):
                    return redirect("/account/profile/")  # MUST return this redirect

        return self.get_response(request)