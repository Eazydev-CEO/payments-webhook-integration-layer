"""
Dashboard metric aggregation.

A single :func:`overview_metrics` builds every KPI shown on the overview page,
scoped to a selectable date window (Today / 7D / 1M / 3M / custom).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.common.retry import DeliveryStatus
from apps.crm.models import CRMDelivery
from apps.payments.models import PaymentIntent, PaymentStatus
from apps.settlements.models import MatchStatus, SettlementItem
from apps.webhooks.models import WebhookEvent

# Presets: label -> number of days back (None => custom).
RANGE_PRESETS = {
    "today": 1,
    "7d": 7,
    "1m": 30,
    "3m": 90,
    "all": None,
}


@dataclass
class DateWindow:
    key: str
    label: str
    start: datetime | None
    end: datetime | None

    def filter_kwargs(self, field: str = "created_at") -> dict:
        kwargs = {}
        if self.start is not None:
            kwargs[f"{field}__gte"] = self.start
        if self.end is not None:
            kwargs[f"{field}__lte"] = self.end
        return kwargs


def resolve_window(request) -> DateWindow:
    """Parse the requested date window from query params."""
    key = (request.GET.get("range") or "7d").lower()
    now = timezone.now()

    if key == "custom":
        start = _parse_date(request.GET.get("start"))
        end = _parse_date(request.GET.get("end"), end_of_day=True)
        return DateWindow("custom", "Custom", start, end)

    days = RANGE_PRESETS.get(key)
    if days is None:
        return DateWindow("all", "All time", None, None)
    if key == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(days=days)
    labels = {"today": "Today", "7d": "7 days", "1m": "1 month", "3m": "3 months"}
    return DateWindow(key, labels.get(key, key), start, now)


def _parse_date(value: str | None, *, end_of_day: bool = False):
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return timezone.make_aware(parsed, timezone.get_current_timezone())


def overview_metrics(window: DateWindow) -> dict:
    """Compute all overview KPIs for the given window."""
    intents = PaymentIntent.objects.filter(**window.filter_kwargs("created_at"))
    webhooks = WebhookEvent.objects.filter(**window.filter_kwargs("received_at"))
    crm = CRMDelivery.objects.filter(**window.filter_kwargs("created_at"))
    settlement_items = SettlementItem.objects.filter(
        **window.filter_kwargs("batch__created_at")
    )

    succeeded = intents.filter(status=PaymentStatus.SUCCEEDED)
    total_volume = succeeded.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    failed_webhook_deliveries = webhooks.filter(
        status__in=[DeliveryStatus.FAILED, DeliveryStatus.PERMANENTLY_FAILED]
    ).count()

    successful_crm = crm.filter(status=DeliveryStatus.SUCCESS).count()

    settlement_mismatches = settlement_items.exclude(
        match_status=MatchStatus.MATCHED
    ).count()

    return {
        "total_volume": total_volume,
        "successful_payments": succeeded.count(),
        "failed_payments": intents.filter(status=PaymentStatus.FAILED).count(),
        "pending_payments": intents.filter(
            status__in=[PaymentStatus.CREATED, PaymentStatus.PROCESSING]
        ).count(),
        "total_intents": intents.count(),
        "duplicate_webhooks": webhooks.filter(is_duplicate=True).count(),
        "failed_webhook_deliveries": failed_webhook_deliveries,
        "successful_crm": successful_crm,
        "failed_crm": crm.filter(
            status__in=[DeliveryStatus.FAILED, DeliveryStatus.PERMANENTLY_FAILED]
        ).count(),
        "settlement_mismatches": settlement_mismatches,
        "verified_webhooks": webhooks.filter(signature_verified=True).count(),
        "rejected_webhooks": webhooks.filter(signature_verified=False).count(),
    }


def volume_series(window: DateWindow, *, buckets: int = 14) -> dict:
    """Daily succeeded-volume series for the overview chart."""
    now = timezone.now()
    start = window.start or (now - timedelta(days=buckets))
    span_days = max((now - start).days, 1)
    step = max(span_days // buckets, 1)

    labels: list[str] = []
    values: list[float] = []
    cursor = start
    while cursor <= now:
        nxt = cursor + timedelta(days=step)
        amount = (
            PaymentIntent.objects.filter(
                status=PaymentStatus.SUCCEEDED,
                created_at__gte=cursor,
                created_at__lt=nxt,
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        labels.append(cursor.strftime("%b %d"))
        values.append(float(amount))
        cursor = nxt
    return {"labels": labels, "values": values}
