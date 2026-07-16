# Security

This is a portfolio project built to production-style standards. Highlights:

## Secrets & configuration
- **No secrets in code.** All credentials come from environment variables
  (`SECRET_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
  `PAYSTACK_SECRET_KEY`, `PAYSTACK_WEBHOOK_SECRET`, `ADMIN_PASSWORD`, …).
- `.env` is **git-ignored**; `.env.example` documents every variable with no
  real values.
- Processor `config` stored in the DB is non-secret display metadata only.

## Webhook signature verification
- Real HMAC verification for Stripe (SHA-256) and Paystack (SHA-512), using
  `hmac.compare_digest` (constant-time) — see `apps/webhooks/signatures.py`.
- Stripe timestamp tolerance (5 min) guards against replay.
- Invalid signatures are rejected (`400`) and stored `signature_verified=False`
  for audit. Raw payload + headers are retained for forensics.
- Demo mode only applies when a secret is **absent**; a present secret is always
  enforced.

## Idempotency
- Payment intents: unique `idempotency_key` + `IntegrityError` fallback prevents
  duplicates even under concurrent requests.
- Webhooks: duplicate `event_id`s are stored but never reprocessed, preventing
  double settlement/CRM fan-out.

## Web app hardening
- **CSRF protection** on all dashboard forms (Django middleware + `{% csrf_token %}`).
  Webhook receivers are explicitly `csrf_exempt` because they are authenticated
  by signature, not by session.
- Session cookies `HttpOnly`; `Secure` cookies + optional SSL redirect/HSTS when
  `DEBUG=False`.
- `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`.
- All dashboard views require authentication (`@login_required`); anonymous
  users are redirected to `/login/`.

## Audit trail
- Every money- or security-relevant action writes an immutable `AuditLog`
  (actor, action, entity, IP, metadata): intent creation, idempotent replays,
  webhook received/verified/rejected/duplicate/processed, CRM delivery outcomes,
  retries, settlement import/reconcile, and login/logout.
- Audit rows are read-only in the Django admin.

## Safe error handling
- Service-layer errors (`PaymentError`, `WebhookError`, `SettlementError`) map to
  clean `400` responses / user-facing messages — no stack traces or internal
  detail leak to clients.

## Production checklist
- Set a strong `SECRET_KEY` and `DEBUG=False`.
- Set `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` to your domain(s).
- Set `STRIPE_WEBHOOK_SECRET` / `PAYSTACK_SECRET_KEY` to enforce signature
  verification per processor; `STRIPE_SECRET_KEY` / `PAYSTACK_SECRET_KEY` drive the
  derived `DEMO_MODE` banner. The two are independent — see `../ENVIRONMENT.md`.
- Put the app behind HTTPS (the compose service is HTTP; terminate TLS at a proxy).
- Set `ADMIN_PASSWORD` to a strong password of your own. There is no default and no
  admin account exists until you set one; to rotate, change the value and re-run
  `create_admin`, which re-applies the environment password on every run.
