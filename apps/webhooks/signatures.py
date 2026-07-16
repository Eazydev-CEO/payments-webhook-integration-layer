"""
Webhook signature verification.

Implements the real HMAC structures used by Stripe and Paystack. When the
corresponding webhook secret is not configured the platform is in demo mode
and accepts the payload (clearly flagged), so the pipeline stays runnable
without live credentials — but a *present* secret is always enforced.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from django.conf import settings


@dataclass
class VerificationResult:
    verified: bool
    note: str
    demo: bool = False

    @property
    def rejected(self) -> bool:
        return not self.verified


# --------------------------------------------------------------------------
# Stripe: header `Stripe-Signature: t=<ts>,v1=<hex hmac-sha256>`
# signed_payload = f"{t}.{raw_body}", key = STRIPE_WEBHOOK_SECRET
# --------------------------------------------------------------------------
def _parse_stripe_header(header: str) -> tuple[str | None, list[str]]:
    timestamp: str | None = None
    signatures: list[str] = []
    for part in header.split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        if key == "t":
            timestamp = value.strip()
        elif key == "v1":
            signatures.append(value.strip())
    return timestamp, signatures


def compute_stripe_signature(payload: str, timestamp: str, secret: str) -> str:
    signed = f"{timestamp}.{payload}".encode()
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def verify_stripe(
    payload: str, header: str, *, tolerance: int = 300
) -> VerificationResult:
    secret = settings.STRIPE_WEBHOOK_SECRET
    if not secret:
        return VerificationResult(True, "demo mode: Stripe secret not set", demo=True)
    if not header:
        return VerificationResult(False, "missing Stripe-Signature header")

    timestamp, signatures = _parse_stripe_header(header)
    if not timestamp or not signatures:
        return VerificationResult(False, "malformed Stripe-Signature header")

    expected = compute_stripe_signature(payload, timestamp, secret)
    if not any(hmac.compare_digest(expected, sig) for sig in signatures):
        return VerificationResult(False, "Stripe signature mismatch")

    # Replay protection.
    try:
        if abs(time.time() - int(timestamp)) > tolerance:
            return VerificationResult(False, "Stripe timestamp outside tolerance")
    except ValueError:
        return VerificationResult(False, "invalid Stripe timestamp")

    return VerificationResult(True, "Stripe signature verified")


# --------------------------------------------------------------------------
# Paystack: header `x-paystack-signature: <hex hmac-sha512>`
# key = PAYSTACK_SECRET_KEY, message = raw_body
# --------------------------------------------------------------------------
def compute_paystack_signature(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha512).hexdigest()


def verify_paystack(payload: str, header: str) -> VerificationResult:
    secret = settings.PAYSTACK_SECRET_KEY
    if not secret:
        return VerificationResult(True, "demo mode: Paystack secret not set", demo=True)
    if not header:
        return VerificationResult(False, "missing x-paystack-signature header")

    expected = compute_paystack_signature(payload, secret)
    if not hmac.compare_digest(expected, header.strip()):
        return VerificationResult(False, "Paystack signature mismatch")
    return VerificationResult(True, "Paystack signature verified")
