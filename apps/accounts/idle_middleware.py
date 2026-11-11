from django.contrib.auth import logout
from django.utils import timezone
from datetime import timedelta


class IdleTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                minutes = request.user.hub_account.idle_timeout_minutes
            except Exception:
                minutes = 30
            last = request.session.get("last_activity")
            now = timezone.now()
            if last:
                last_dt = timezone.datetime.fromisoformat(last)
                if now - last_dt > timedelta(minutes=minutes):
                    logout(request)
                    return self.get_response(request)
            request.session["last_activity"] = now.isoformat()
        return self.get_response(request)