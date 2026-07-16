"""Audit logging helpers used across the platform."""
from __future__ import annotations

from typing import Any

from .models import AuditLog


def record_audit(
    action: str,
    summary: str,
    *,
    actor=None,
    entity_type: str = "",
    entity_id: str | int = "",
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Create an :class:`AuditLog` row. Never raises on best-effort logging."""
    return AuditLog.objects.create(
        action=action,
        summary=summary[:255],
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        entity_type=entity_type,
        entity_id=str(entity_id),
        metadata=metadata or {},
        ip_address=ip_address,
    )


def client_ip(request) -> str | None:
    """Best-effort client IP extraction (honours a single proxy hop)."""
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
