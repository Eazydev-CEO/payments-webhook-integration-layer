# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 1.0.x   | Yes       |
| < 1.0   | No        |

Fixes are applied to the latest 1.0.x release only.

## Reporting a vulnerability

Please report security issues **privately**, not as a public issue.

Use GitHub private vulnerability reporting on this repository: open the
**Security** tab, then **Report a vulnerability**. This opens a private draft
advisory visible only to the maintainer and you.

The aim is to acknowledge a report within **5 business days**. This is a
best-effort target for a personal portfolio project, not a guarantee or an SLA.
There is no bug bounty.

### What to include

- Affected version, commit or tag, and how the app was run (Docker, local).
- Relevant configuration: `DEBUG`, whether `DEMO_MODE` was active, and which
  processor secrets were set. **Never include real secret values.**
- The component involved — webhook receiver, payment intent API, dashboard view,
  settlement import, etc.
- Reproduction steps, a request/response pair or payload, and observed vs
  expected behaviour.
- Impact you believe it has, and any suggested fix.

## Scope

PayBridge is a **portfolio / demonstration project**, not a hosted service.
There is no production deployment to test against, and no bounty programme.
It ships in demo mode with simulated processors and seeded demo data; payment
capture and CRM fan-out are simulated, and no real money moves.

In scope: the code in this repository — signature verification, idempotency,
duplicate suppression, retry logic, reconciliation, dashboard, and API.

Out of scope: findings against Stripe, Paystack, or any other third party.
**Do not test, probe or attack third-party processors.** Report issues in their
products to those vendors directly. Also out of scope: findings that depend on
deliberately insecure development defaults that the documentation already tells
you to change (for example running with `DEBUG=True` or without a real
`SECRET_KEY`) — see the hardening checklist below.

## Security posture

The controls implemented in this codebase, in brief:

- **Webhook signature verification** — real HMAC, constant-time compared
  (`hmac.compare_digest`): Stripe HMAC-SHA256 over `{timestamp}.{raw_body}` from
  the `Stripe-Signature` header (`t=`, `v1=`), keyed by `STRIPE_WEBHOOK_SECRET`,
  with a 300-second replay tolerance on the timestamp; Paystack HMAC-SHA512 over
  the raw body from `x-paystack-signature`, keyed by `PAYSTACK_SECRET_KEY`
  (matching Paystack's real scheme). Enforcement is decided **per processor by
  the presence of that processor's secret**, not by the `DEMO_MODE` flag: a
  present secret is always strictly enforced, and demo acceptance applies only
  when the secret is absent. Rejected payloads are still stored — raw payload and
  headers retained, `signature_verified=False`, status `permanently_failed`, a
  `webhook_rejected` audit entry, and a `400` to the caller.
- **Idempotency** — `PaymentIntent.idempotency_key` is unique with an
  `IntegrityError` race fallback, so a replay returns the original intent rather
  than creating a duplicate. Re-delivered webhook `event_id`s are stored for
  audit but flagged as duplicates and never reprocessed, preventing double
  settlement and double CRM fan-out.
- **CSRF protection** on all dashboard forms. Webhook receivers are explicitly
  CSRF-exempt (and `AllowAny`, with no authentication classes) because the
  processor signature *is* their authentication.
- **Session authentication** — every dashboard view requires login; anonymous
  users are redirected to `/login/`. The REST API uses the same session auth;
  management endpoints require `IsAuthenticated`. `X-Frame-Options: DENY` and
  `X-Content-Type-Options: nosniff` are always set, as is
  `SESSION_COOKIE_HTTPONLY`. `CSRF_COOKIE_HTTPONLY` is deliberately `False` —
  dashboard JS reads the token from the cookie for `fetch`.
- **Cookie hardening under `DEBUG=False`** — `SESSION_COOKIE_SECURE`,
  `CSRF_COOKIE_SECURE` and `SECURE_PROXY_SSL_HEADER` are applied automatically.
  SSL redirect and HSTS are **opt-in and default to off** (`SECURE_SSL_REDIRECT`
  defaults `False`, `SECURE_HSTS_SECONDS` defaults `0`); they are read only when
  `DEBUG` is false.
- **Env-only secrets** — no credentials in code or in the database. `.env` is
  git-ignored; `.env.example` documents every variable with placeholders only.
  The admin account is created **only** when `ADMIN_PASSWORD` is supplied.
- **Audit logging** — every money- or security-relevant action writes an
  `AuditLog` row (`actor`, `action`, `entity_type`, `entity_id`, `summary`,
  `metadata`, `ip_address`, `created_at`) across 16 actions, including
  `webhook_rejected`, `login` and `logout`. It is append-only by design: nothing
  in the application updates a row, and the Django admin exposes it with every
  field read-only and adding disabled.
- **Safe error messages** — service-layer errors (`PaymentError`,
  `WebhookError`, `SettlementError`) map to clean `400 {"detail": ...}`
  responses rather than leaking internals. This is not a substitute for
  `DEBUG=False`, which is what suppresses Django's own tracebacks.

Implementation detail and file references: [docs/SECURITY.md](docs/SECURITY.md).

## If you are deploying this

This project defaults to a developer-friendly configuration. Before exposing it
to any network you do not control:

- [ ] Set a real `SECRET_KEY` — if unset or empty, an obviously-insecure
      development key is used and the app still boots silently. Generate one
      with:

      docker compose run --rm --entrypoint "" web \
        python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"

- [ ] Set `DEBUG=False`. This is what activates the secure-cookie block.
- [ ] Set `ALLOWED_HOSTS` to your real hostname(s).
- [ ] Set `CSRF_TRUSTED_ORIGINS` to your real origin(s), scheme included.
- [ ] Terminate **HTTPS/TLS at a reverse proxy** — the container serves plain
      HTTP on port 8000. Then set `SECURE_SSL_REDIRECT=True` and a non-zero
      `SECURE_HSTS_SECONDS`; both are off by default even when `DEBUG=False`.
- [ ] Set a strong `ADMIN_PASSWORD` in your environment before running
      `create_admin` — no password default exists, and without it the admin
      account is simply not created. To rotate, change the environment value and
      re-run the command: `create_admin` re-applies the env password on every
      run, and the container entrypoint runs it (via `seed_demo_data`) on every
      start, so a password changed only in the UI is overwritten on restart.
- [ ] Enforce Stripe signatures by setting `STRIPE_WEBHOOK_SECRET`, and Paystack
      signatures by setting `PAYSTACK_SECRET_KEY`. Note `PAYSTACK_WEBHOOK_SECRET`
      is read into settings for symmetry but is **not consumed by any code
      path** — setting it has no effect.
- [ ] Clear the `DEMO_MODE=True` line that `.env.example` ships. `DEMO_MODE` only
      drives the demo badge in the UI; it never relaxes or tightens signature
      enforcement, but pinning it `True` overrides the derivation and leaves the
      deployment mislabelled.
- [ ] Review the seeded demo data — the entrypoint seeds it on every start — and
      remove the seeding step if it is not wanted.

See [ENVIRONMENT.md](ENVIRONMENT.md) for every variable and
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for deployment notes.
