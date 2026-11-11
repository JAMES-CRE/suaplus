"""Audit service - writes to the AuditEvent table."""

from django.utils import timezone
from apps.audit.models import AuditEvent
import json

def log_event(request, action, entity, entity_id, before=None, after=None):
    
    actor = request.user if request and request.user.is_authenticated else None
    ip = request.META.get("REMOTE_ADDR") if request else None
    
    AuditEvent.objects.create(
        actor=actor,
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        before=before,
        after=after,
        ip=ip,
    )