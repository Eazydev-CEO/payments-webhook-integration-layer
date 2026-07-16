"""
CRM delivery layer.

Normalized payment events are fanned out to external CRMs. In demo mode the
network call is *simulated* deterministically so the pipeline (including
retry/backoff) is fully exercisable without real CRM credentials.
"""
from __future__ import annotations

import hashlib

from apps.audit.services import record_audit
from apps.common.retry import DeliveryStatus

from .models import CRMDelivery, CRMTarget

# CRMs that every normalized event is forwarded to.
DEFAULT_TARGETS = [CRMTarget.HUBSPOT, CRMTarget.ZOHO, CRMTarget.INTERNAL]


def enqueue_crm_deliveries(
    *,
    payment_intent,
    event_type: str,
    source_event_id: str,
    payload: dict,
    targets: list[str] | None = None,
) -> list[CRMDelivery]:
    """Create pending CRM deliveries for a normalized event (idempotent per event+target)."""
    deliveries = []
    for target in targets or DEFAULT_TARGETS:
        delivery, _ = CRMDelivery.objects.get_or_create(
            target=target,
            source_event_id=source_event_id,
            defaults={
                "payment_intent": payment_intent,
                "event_type": event_type,
                "payload": payload,
                "status": DeliveryStatus.PENDING,
            },
        )
        deliveries.append(delivery)
    return deliveries


def _simulate_crm_call(delivery: CRMDelivery) -> tuple[bool, dict]:
    """Deterministic demo CRM call.

    Fails on the first attempt for a stable ~30% slice of deliveries so the
    retry system visibly recovers them. Real integrations would issue an
    HTTP request here and inspect the response.
    """
    seed = f"{delivery.target}:{delivery.source_event_id}"
    digest = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    flaky = (digest % 10) < 3  # ~30% flaky on first try

    if flaky and delivery.retry_count == 0:
        return False, {"simulated": True, "http_status": 503, "reason": "CRM timeout"}
    return True, {
        "simulated": True,
        "http_status": 200,
        "crm_ref": f"{delivery.target}_{digest % 1_000_000:06d}",
    }


def deliver_crm(delivery: CRMDelivery, *, actor=None) -> bool:
    """Attempt a single CRM delivery, updating retry/backoff state."""
    delivery.mark_processing()
    ok, response = _simulate_crm_call(delivery)
    delivery.response = response
    delivery.save(update_fields=["response"])

    if ok:
        delivery.mark_success()
        record_audit(
            "crm_delivered",
            f"CRM {delivery.target} delivery succeeded for {delivery.event_type}",
            actor=actor,
            entity_type="CRMDelivery",
            entity_id=delivery.pk,
            metadata=response,
        )
        return True

    delivery.mark_failure(response.get("reason", "CRM delivery failed"))
    record_audit(
        "crm_failed",
        f"CRM {delivery.target} delivery failed ({delivery.status})",
        actor=actor,
        entity_type="CRMDelivery",
        entity_id=delivery.pk,
        metadata=response,
    )
    return False


def retry_crm_delivery(delivery: CRMDelivery, *, actor=None) -> bool:
    """Operator-triggered manual retry of a failed CRM delivery."""
    delivery.reset_for_manual_retry()
    record_audit(
        "crm_retried",
        f"Manual retry of CRM {delivery.target} delivery #{delivery.pk}",
        actor=actor,
        entity_type="CRMDelivery",
        entity_id=delivery.pk,
    )
    return deliver_crm(delivery, actor=actor)


def process_due_crm_deliveries(*, limit: int = 100) -> dict[str, int]:
    """Process all CRM deliveries whose backoff window has elapsed."""
    due = [
        d
        for d in CRMDelivery.objects.filter(
            status__in=[DeliveryStatus.PENDING, DeliveryStatus.FAILED]
        ).order_by("next_retry_at")[:limit]
        if d.is_due
    ]
    succeeded = failed = 0
    for delivery in due:
        if deliver_crm(delivery):
            succeeded += 1
        else:
            failed += 1
    return {"processed": len(due), "succeeded": succeeded, "failed": failed}
