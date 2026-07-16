"""Presentation helpers for dashboard templates."""
from __future__ import annotations

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# status value -> (badge css class, human label)
_BADGES = {
    # payment intent
    "created": ("b-blue", "Created"),
    "processing": ("b-amber", "Processing"),
    "succeeded": ("b-green", "Succeeded"),
    "failed": ("b-red", "Failed"),
    "canceled": ("b-gray", "Canceled"),
    "refunded": ("b-violet", "Refunded"),
    "pending": ("b-gray", "Pending"),
    # delivery lifecycle
    "success": ("b-green", "Success"),
    "permanently_failed": ("b-red", "Permanently failed"),
    # reconciliation match status
    "matched": ("b-green", "Matched"),
    "amount_mismatch": ("b-amber", "Amount mismatch"),
    "currency_mismatch": ("b-violet", "Currency mismatch"),
    "missing": ("b-red", "Missing"),
    "unknown": ("b-gray", "Unknown"),
    # settlement batch
    "reconciled": ("b-green", "Reconciled"),
}


@register.simple_tag
def status_badge(value, label: str = "") -> str:
    key = (value or "").lower()
    css, default_label = _BADGES.get(key, ("b-gray", (value or "—").replace("_", " ").title()))
    text = label or default_label
    return mark_safe(f'<span class="badge-pill {css}"><span class="dot"></span>{text}</span>')


@register.filter
def money(value) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


@register.filter
def pct(part, whole) -> float:
    try:
        whole = float(whole)
        if whole == 0:
            return 0.0
        return round(float(part) / whole * 100, 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
