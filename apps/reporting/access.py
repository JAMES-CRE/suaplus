"""Request/job-local scope for the existing pure KPI API.

ContextVar keeps concurrent requests isolated. Views and memo jobs establish
an explicit actor/capability before evaluating any aggregate; every base model
query in kpis.py passes through records(). No managers are monkeypatched.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from django.core.exceptions import PermissionDenied

from apps.accounts.permissions import can, scope_records

_report_actor = ContextVar("suaplus_report_actor", default=None)


@contextmanager
def reporting_scope(user, capability="view_dashboard"):
    if not can(user, capability):
        raise PermissionDenied
    token = _report_actor.set((user, capability))
    try:
        yield
    finally:
        _report_actor.reset(token)


def scoped_reporting(capability="view_dashboard"):
    def decorator(view):
        @wraps(view)
        def scoped(request, *args, **kwargs):
            with reporting_scope(request.user, capability):
                return view(request, *args, **kwargs)
        return scoped
    return decorator


def records(model):
    actor = _report_actor.get()
    if actor is None:
        return model.objects
    user, capability = actor
    return scope_records(model.objects.all(), user, capability)
