from celery import shared_task
from apps.programmes.services.tier_engine import compute_tier_status

@shared_task
def run_tier_engine_task():
    """Nightly task to compute trainer tiers."""
    summary = compute_tier_status()
    return summary
