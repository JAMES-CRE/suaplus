from django.core.cache import cache
from django.http import HttpResponse
from functools import wraps
from django.conf import settings


def rate_limit(key_prefix, max_requests=10, window_seconds=3600):
    """Decorator: limit requests per IP per window."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # BYPASS RATE LIMITING DURING PERFORMANCE TESTS
            if getattr(settings, "BYPASS_RATE_LIMIT", False):
                return view_func(request, *args, **kwargs)

            ip = request.META.get("REMOTE_ADDR", "unknown")
            cache_key = f"rl:{key_prefix}:{ip}"
            count = cache.get(cache_key, 0)
            if count >= max_requests:
                return HttpResponse(
                    "Too many requests. Please try again later.",
                    status=429,
                )
            cache.set(cache_key, count + 1, timeout=window_seconds)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator