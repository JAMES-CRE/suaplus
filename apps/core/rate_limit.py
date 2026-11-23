from functools import wraps
import hashlib
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


def rate_limit(key_prefix, max_requests=10, window_seconds=3600):
    """Decorator: limit requests per IP per window."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # BYPASS RATE LIMITING DURING PERFORMANCE TESTS
            if getattr(settings, "BYPASS_RATE_LIMIT", False):
                return view_func(request, *args, **kwargs)

            ip = request.META.get("REMOTE_ADDR", "unknown")
            digest = hashlib.sha256(ip.encode()).hexdigest()
            bucket = int(time.time() // window_seconds)
            cache_key = f"rl:{key_prefix}:{digest}:{bucket}"
            if cache.add(cache_key, 1, timeout=window_seconds + 1):
                count = 1
            else:
                try:
                    count = cache.incr(cache_key)
                except ValueError:
                    cache.add(cache_key, 1, timeout=window_seconds + 1)
                    count = 1
            if count > max_requests:
                response = HttpResponse("Too many requests. Please try again later.", status=429)
                response["Retry-After"] = str(window_seconds)
                return response
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator