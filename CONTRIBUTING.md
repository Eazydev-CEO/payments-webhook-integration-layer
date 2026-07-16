# Contributing

Thanks for your interest in the Payments & Webhook Integration Layer. This document covers how to get the
stack running, the conventions the codebase follows, and the invariants a change must not break.

## Getting set up

The project is Docker-first. You do not need a local Python environment.

1. Fork the repository, then clone your fork:

   ```bash
   git clone https://github.com/<your-username>/payments-webhook-integration-layer.git
   cd payments-webhook-integration-layer
   ```

2. Create your environment file:

   ```bash
   cp .env.example .env
   ```

3. Edit `.env` and set, at minimum:

   - `SECRET_KEY` — generate one:
     `python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"`
     If left empty, `config/settings.py` falls back to an obviously-insecure development key.
   - `ADMIN_PASSWORD` — choose your own. There is no default and no password is stored in this repo. The
     admin account is created **only** when `ADMIN_PASSWORD` is set. `ADMIN_USERNAME` defaults to `admin`,
     `ADMIN_EMAIL` to `admin@example.com`.

   Processor secrets (`STRIPE_SECRET_KEY`, `PAYSTACK_SECRET_KEY`, and their webhook secrets) are optional.
   Leave them empty and the stack runs in demo mode, which is the normal contributor setup.

4. Start it:

   ```bash
   docker compose up --build -d
   ```

   On Windows, use the helper instead — it picks the port for you:

   ```powershell
   .\scripts\run_free_port.ps1
   ```

### About `APP_PORT`

There is no fixed host port. The container always listens on `8000`; Compose maps `${APP_PORT}:8000`.
`scripts/run_free_port.ps1` selects a random free port in `[10000, 60000]`, verifies it is genuinely free by
binding a listener, writes `APP_PORT` and a matching `CSRF_TRUSTED_ORIGINS` into `.env`, then brings the stack
up and prints the URL. It never stops or kills another process.

If you start Compose by hand, set `APP_PORT` in `.env` yourself and keep `CSRF_TRUSTED_ORIGINS` in sync
(for example `http://localhost:<your-port>,http://127.0.0.1:<your-port>`). Never hardcode a port in code,
docs, or tests — read it from the environment.

Log in with the `ADMIN_USERNAME` / `ADMIN_PASSWORD` you set. The container entrypoint runs `migrate`, then
`seed_demo_data`, then `collectstatic`, then gunicorn.

## Project layout

| Path | Responsibility |
| --- | --- |
| `config/` | Settings, root URLconf, WSGI/ASGI entrypoints |
| `apps/common/` | Shared primitives — `retry.py` holds `RetryableJob`, `DeliveryStatus`, backoff maths |
| `apps/processors/` | `PaymentProcessor` model, `ProcessorCode` choices, `ensure_default_processors()` |
| `apps/payments/` | `PaymentIntent` / `PaymentTransaction`, idempotent intent creation |
| `apps/webhooks/` | Ingestion: `signatures.py`, `normalize.py`, `simulate.py`, `services.py`, retry runner |
| `apps/crm/` | `CRMDelivery`, `CRMTarget`, simulated fan-out to HubSpot / Zoho / internal |
| `apps/settlements/` | Settlement batches, CSV import, reconciliation and match flags |
| `apps/audit/` | Append-only `AuditLog` and `record_audit()` |
| `apps/accounts/` | Session login/logout, `create_admin` command |
| `apps/dashboard/` | Operator UI views, `metrics.py`, `seed_demo_data` command |
| `apps/api/` | DRF serializers and viewsets; routes live in `apps/api_urls.py` |
| `tests/` | Django `TestCase` suites |
| `docs/` | Deep-dive documentation and screenshots |

### Where business logic goes

**Business logic belongs in `apps/<app>/services.py`. Views and viewsets stay thin.**

The dashboard and the REST API are two front doors onto the same behaviour. Both call the same service
functions, so a rule enforced in a service is enforced everywhere. A view should validate input, call one
service function, and render or serialize the result. If you find yourself writing a state transition, a
signature check, or an audit call inside a view, move it into the service layer.

`apps/api/` and `apps/dashboard/` deliberately have no `services.py` — they are presentation layers.

## Invariants

These are load-bearing. A change that breaks one of them will not be merged.

- **Idempotency — payment intents.** `PaymentIntent.idempotency_key` is unique. `create_payment_intent()`
  returns the *original* intent on replay with `created=False`, which the API surfaces as
  `"idempotent_replay": true` and HTTP 200 (201 only on first create). The `IntegrityError` fallback that
  resolves a concurrent race must stay.
- **Idempotency — webhooks.** A re-delivered `event_id` is **stored** for audit, flagged `is_duplicate=True`,
  and **never reprocessed**. There is deliberately **no DB unique constraint** on `(processor, event_id)` —
  duplicates must remain storable. Do not add one. `process_webhook_event()` returns early for duplicates.
- **Signature verification is enforced whenever a secret is present.** Demo mode applies only when the secret
  is *absent*. A present `STRIPE_WEBHOOK_SECRET` or `PAYSTACK_SECRET_KEY` is always strictly enforced. Never
  add a bypass, an env flag that skips verification, or a "trusted" path. Always compare with
  `hmac.compare_digest`. Keep Stripe's timestamp tolerance (300s replay protection).
- **Shared retry lifecycle.** `WebhookEvent` and `CRMDelivery` both extend `RetryableJob`. Use
  `mark_processing()` / `mark_success()` / `mark_failure()` / `reset_for_manual_retry()` and the `is_due`
  property. Do not hand-roll retry counters, status strings, or backoff timers. Backoff is
  `RETRY_BASE_SECONDS * 2**retry_count`, capped at `RETRY_MAX_BACKOFF_SECONDS`.
- **Downstream code consumes the normalized shape, never a raw payload.** `normalize_event()` is the only
  place that understands a processor dialect. Everything after it — payment updates, CRM fan-out,
  reconciliation — reads `event.normalized`. If you need a new field downstream, add it to the normalized
  shape for *every* processor rather than reaching into `raw_payload`.
- **Auth boundaries.** Webhook receivers are intentionally `csrf_exempt` with `AllowAny` and no authentication
  classes: the signature *is* the authentication. Every other API viewset requires `IsAuthenticated`, and every
  dashboard view is login-required (all 17 of them). Do not relax either side.
- **Audit money- and security-relevant actions.** Call `record_audit(...)` for intent creation, replays,
  payment outcomes, webhook receipt/verification/rejection/duplication/processing/retry, CRM delivery outcomes,
  and settlement imports. `AuditLog` is append-only — never update or delete rows.
- **Secrets only via environment.** No credential ever lands in code, fixtures, tests, or docs.
  `PaymentProcessor.config` is for non-secret display config only.

## Adding a new payment processor

Work through all seven steps — a partial integration will fail tests.

1. **`apps/processors/models.py`** — add a `ProcessorCode` entry (e.g. `ADYEN = "adyen", "Adyen"`).
2. **`apps/webhooks/signatures.py`** — add a `verify_<processor>()` returning a `VerificationResult`, following
   the existing shape: return a `demo=True` result when the secret is absent, enforce strictly when present,
   and use `hmac.compare_digest`. Wire it into the `_verify()` dispatcher in `apps/webhooks/services.py`.
3. **`apps/webhooks/normalize.py`** — add a branch to `extract_event_meta()` (returning `(event_id, raw_type)`)
   and to `normalize_event()`, mapping the processor's event types onto `payment.succeeded` /
   `payment.failed` / `payment.updated` and emitting the full internal shape.
4. **`apps/webhooks/simulate.py`** — add a `build_<processor>_event()` builder that produces a realistically
   shaped, correctly signed payload, and register it in the `BUILDERS` map.
5. **`apps/api/views.py`** — subclass `BaseWebhookView` with your `processor_code`, then add the route to
   `apps/api_urls.py` as `webhooks/<processor>/`.
6. **`apps/processors/services.py`** — add an entry to `DEFAULT_PROCESSORS` so `ensure_default_processors()`
   creates it. Add any new secret env vars to `config/settings.py`, `.env.example`, and `ENVIRONMENT.md`.
7. **`tests/test_webhooks.py`** — cover signature accept/reject, normalization, and duplicate suppression for
   the new processor.

## Running tests

```bash
docker compose run --rm --entrypoint "" web python manage.py test
```

The suite is **29 tests and must stay green**. New behaviour needs new tests.

> **Run the tests in Docker.** Django 5.1's test client is incompatible with Python 3.14 (a `copy()` bug in
> template-context instrumentation), and the image pins `python:3.12-slim`. Service-layer tests pass on 3.14,
> but the template/view tests need 3.12 or 3.13. `--entrypoint ""` skips the migrate/seed/collectstatic
> entrypoint so the test runner starts directly.

See `docs/TESTING.md` for the suite breakdown.

## Migrations

Every model change needs a migration committed alongside it.

The image copies the source at build time and there is **no bind mount**, so a migration generated inside the
container will not appear in your working tree by itself — copy it back:

```bash
docker compose exec web python manage.py makemigrations
docker compose cp web:/app/apps/<app>/migrations/<generated_file>.py apps/<app>/migrations/
docker compose exec web python manage.py migrate
```

Rebuild (`docker compose up --build -d`) so the image picks up the committed migration. Review the generated
file before committing — never edit an already-released migration.

## Code style

- Match the surrounding code; it is the style guide.
- Type hints where they add clarity — service signatures, return types, dataclasses. Modules use
  `from __future__ import annotations`.
- Keyword-only arguments for service functions, as the existing ones do.
- Clear names over short ones. Docstrings explain *why* a rule exists (the idempotency and signature modules
  are good examples). Comments only where they add value — no narration of obvious code.
- Domain errors are typed exceptions (`PaymentError`, `WebhookError`, `SettlementError`) that views map to
  HTTP 400.

## Commits and pull requests

Use Conventional-Commit style prefixes:

```
feat: add Adyen webhook receiver
fix: keep timestamp tolerance on replayed Stripe events
docs: document the reconciliation match flags
refactor: move CRM fan-out into the service layer
test: cover duplicate suppression for Paystack
chore: bump drf-spectacular
```

- One logical change per pull request. Describe what changed, why, and how you verified it.
- Confirm the 29 tests pass before opening the PR.
- Update the docs your change touches:
  - new or changed endpoints → `docs/API.md`
  - new or changed env vars → `ENVIRONMENT.md` **and** `.env.example`
  - user-visible features → `FEATURES.md`
  - releases → `CHANGELOG.md`
  - webhook/reconciliation behaviour → `docs/WEBHOOKS.md`, `docs/RECONCILIATION.md`
- **Never commit `.env`, a database file, screenshots containing real data, or any credential.** `.gitignore`
  covers `.env`, `*.sqlite3`, and key material — do not force-add past it.
- No local absolute paths in code or docs. Use repo-relative paths and clone-relative commands.

## Reporting bugs

Open an issue with:

- what you expected and what happened
- steps to reproduce, including the relevant `.env` settings with **secrets redacted**
- whether you were in demo mode or had a processor secret configured
- relevant output from `docker compose logs web`

## Security issues

**Do not open a public issue for a security vulnerability.** Follow the disclosure process in `SECURITY.md`.
The threat model and the platform's security design are documented in `docs/SECURITY.md`.
