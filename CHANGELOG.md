# Changelog

All notable changes to PayBridge are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [1.0.0] - 2026-07-08

Initial production-ready release.

### Added
- **Auth & accounts**: Django session auth for a single admin/operator; login
  and logout views (`accounts:login` / `accounts:logout`) with a dark-glass
  login screen; `create_admin` seeds the account idempotently from
  `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` and re-aligns it on
  re-run. `ADMIN_USERNAME` defaults to `admin` and `ADMIN_EMAIL` to
  `admin@example.com`; **`ADMIN_PASSWORD` has no default** — the command skips
  account creation entirely until you set one in `.env`. Login/logout audited
  via `user_logged_in` / `user_logged_out` signals with client IP; Django admin
  retained at `/django-admin/`.
- **Processors**: `PaymentProcessor` registry over three codes — `stripe`,
  `paystack`, `manual` (internal demo); `ensure_default_processors()`
  idempotent bootstrap; `is_live` / `mode_label` derived from the configured
  env secrets so the Settings page shows Live vs Demo per processor; the model
  stores non-secret display config only — credentials live in env vars.
- **Idempotent payment intents**: `PaymentIntent` with a unique `reference`
  (`pi_<hex>`) and a unique caller-supplied `idempotency_key`;
  `create_payment_intent()` returns the original intent (`created=False`) on
  replay instead of duplicating, and falls back to the winner's row when a
  concurrent request loses the race on the unique key; decimal + positive
  amount validation; `PaymentTransaction` records concrete money movements
  against an intent; `mark_demo_payment()` settles a demo/manual intent as
  success/failed with a matching transaction.
- **Webhook ingestion**: three receivers — `/api/webhooks/stripe/`,
  `/api/webhooks/paystack/`, `/api/webhooks/internal/` — open and CSRF-exempt
  because the processor signature *is* the authentication.
- **Real signature verification**: Stripe HMAC-SHA256 over `{t}.{raw_body}`
  parsed from the `Stripe-Signature` header (`t=`, `v1=`) keyed by
  `STRIPE_WEBHOOK_SECRET`, with a 300-second replay tolerance on the
  timestamp; Paystack HMAC-SHA512 over the raw body from
  `x-paystack-signature` keyed by `PAYSTACK_SECRET_KEY`; constant-time
  comparison throughout. A **present secret is always enforced**; when that
  processor's secret is absent its receiver falls back to demo acceptance with
  the reason recorded in `verification_note`. The internal/manual receiver is
  always demo — it has no signature scheme.
- **Rejected-but-stored**: a failed verification still persists the raw
  payload and headers with `signature_verified=False` and status
  `permanently_failed`, emits a `webhook_rejected` audit entry, and answers
  `400` — nothing is silently dropped.
- **Duplicate suppression**: a re-delivered event is stored for audit, flagged
  `is_duplicate=True` and never reprocessed; idempotency is enforced in the
  service layer against the first non-duplicate `(processor, event_id)`, with
  no DB unique constraint on that pair on purpose, so duplicates stay storable
  rather than raising. Every ingest returns the same envelope — `received`,
  `accepted`, `duplicate`, `verified`, `note`, `event_id`.
- **Normalization**: Stripe, Paystack and manual dialects are translated into
  one internal shape (`payment.succeeded` / `payment.failed` /
  `payment.updated`) so payment updates, CRM fan-out and reconciliation only
  ever see a single format; minor-unit (cents/kobo) to major-unit conversion;
  a stable synthetic event id is derived for Paystack, which sends none.
- **Shared retry/backoff**: `RetryableJob` abstract base + `DeliveryStatus`
  (`pending` / `processing` / `success` / `failed` / `permanently_failed`)
  backing both webhook events and CRM deliveries;
  `compute_backoff_seconds()` = `RETRY_BASE_SECONDS * 2**retry_count`, capped
  at `RETRY_MAX_BACKOFF_SECONDS`, giving up at `RETRY_MAX_ATTEMPTS`
  (defaults 30s / 3600s / 5); operator-triggered `reset_for_manual_retry()`
  makes a job eligible immediately.
- **Retry runner**: `python manage.py process_webhook_retries [--limit N]`
  (default 100) drains both due queues — webhook events and CRM deliveries —
  and prints processed/succeeded/failed counters per queue. Intended for cron.
- **Attempt trail**: every webhook processing attempt writes a
  `WebhookDeliveryAttempt` row (attempt number, success/failed, detail) beside
  the event's `retry_count`, `next_retry_at` and `last_error`.
- **CRM fan-out**: each normalized event is forwarded to HubSpot, Zoho and
  Internal CRM targets; `enqueue_crm_deliveries()` is idempotent per
  event + target; the demo call is deterministic and fails on first attempt
  for a stable ~30% slice so the retry system visibly recovers deliveries;
  manual retry from both the dashboard and the API.
- **Settlements & reconciliation**: CSV import requiring `reference`, `amount`,
  `currency`, `status` columns, imported and reconciled in one atomic
  operation; five-way classification — `matched`, `amount_mismatch`,
  `currency_mismatch`, `unknown` (settlement line matching nothing internal)
  and `missing` (a succeeded internal payment absent from the statement);
  expected vs received vs difference totals plus a stored summary count map;
  re-reconcile rebuilds from stored raw rows.
- **Audit log**: immutable `AuditLog` capturing actor, action, entity
  type/id, summary, JSON metadata and IP across 16 actions — intent created /
  duplicate, demo payment marked, webhook received / verified / rejected /
  duplicate / processed / retried, CRM delivered / failed / retried,
  settlement imported, reconciled, login, logout; filterable viewer page.
- **Operator dashboard**: dark-glass UI over 11 pages — Overview,
  Payment Intents, Intent detail, Transactions, Webhook Events, Failed
  Webhooks, CRM Deliveries, Settlements, Reconciliation, Audit Logs, Settings/
  Processors; KPI set from `overview_metrics()` (volume, successful/failed/
  pending payments, duplicate webhooks, failed deliveries, CRM success/failure,
  settlement mismatches, verified/rejected webhooks); Today / 7D / 1M / 3M /
  All range presets plus a custom start–end window; daily succeeded-volume
  Chart.js series; **Simulate webhook** builds a realistically-shaped, signed
  payload for the intent's processor and runs it through `ingest_webhook()` —
  the same service the live receivers call, so verification, duplicate
  suppression, normalization and CRM fan-out all execute unchanged;
  per-page filters, search and pagination; `status_badge` / `money` /
  `pct` template tags; light-mode toggle persisted to `localStorage`.
- **REST API**: DRF with session auth and a `PageNumberPagination` page size of
  25; routed resources for payment intents (list/retrieve/create + `mark`),
  webhook events (+ `retry`), CRM deliveries (+ `retry`) and settlements
  (+ `import`); intent creation answers `201` on first create and `200` with
  `"idempotent_replay": true` on replay; filter/search/ordering backends;
  drf-spectacular schema at `/api/schema/` with Swagger UI at `/api/docs/` and
  ReDoc at `/api/redoc/`.
- **Demo data**: `seed_demo_data [--fresh]` drives the real service layer with
  a fixed random seed, so idempotency, signature verification, retry/backoff,
  CRM fan-out and audit logs are all produced organically; idempotent unless
  `--fresh`; deliberate settlement discrepancies feed the reconciliation view;
  intents are backdated across ~30 days. Produces 32 intents, 26 webhooks,
  72 CRM deliveries, 2 settlements, 191 audit logs.
- **Docker workflow**: `python:3.12-slim` image; entrypoint runs
  `migrate` → `seed_demo_data` → `collectstatic` → gunicorn (3 workers,
  port 8000); compose maps `${APP_PORT}:8000` so the container port is fixed
  while the host port is a random free one; `scripts/run_free_port.ps1` picks a
  random port in `[10000, 60000]`, confirms it free via `Get-NetTCPConnection`
  plus a real `TcpListener` bind, writes `APP_PORT` and matching
  `CSRF_TRUSTED_ORIGINS` into `.env`, then runs `docker compose up --build -d`
  — it never stops or kills any process; SQLite persists on the named volume
  `app_db` mounted at `/data` (`DB_DIR`); WhiteNoise compressed static files.
- **Tests**: 29-test suite across payments, webhooks, retry/backoff,
  settlements and dashboard, run with
  `docker compose run --rm --entrypoint "" web python manage.py test`.
- **Docs**: README, FEATURES, ENVIRONMENT, SECURITY and CONTRIBUTING at the
  root, plus PROJECTFLOW, API, WEBHOOKS, RECONCILIATION, SECURITY, TESTING and
  DEPLOYMENT under `docs/`; `docs/sample_settlement.csv` for the import flow
  and dashboard screenshots under `docs/screenshots/`.

### Known limitations
- **Unsigned payloads are accepted until you configure secrets.** Each receiver
  is gated independently by its own secret: the Stripe receiver enforces
  signatures only once `STRIPE_WEBHOOK_SECRET` is set, and the Paystack
  receiver only once `PAYSTACK_SECRET_KEY` is set. Setting `STRIPE_SECRET_KEY`
  alone does *not* enforce Stripe webhooks — the webhook secret is the gate.
  The `DEMO_MODE` setting (default: on unless `STRIPE_SECRET_KEY` or
  `PAYSTACK_SECRET_KEY` is present) only drives the demo banner in the UI; it
  does not gate verification.
- **CRM delivery is simulated**, not real HTTP — `_simulate_crm_call()` returns
  a deterministic hash-derived response. A real integration would issue the
  request and inspect the response at that seam.
- **SQLite plus a cron-driven retry runner**, not a broker or task queue. There
  is no Celery; failed jobs only advance when `process_webhook_retries` runs.
- **Management endpoints use session auth.** There are no machine tokens or API
  keys yet, so server-to-server callers must hold a session. Webhook receivers
  are the exception — they authenticate by signature.
- **Bootstrap Icons 1.11.3 and Chart.js 4.4.1 load from the jsDelivr CDN**, so
  icons and the overview chart need outbound network access.
- **Tests run in Docker.** Local Python 3.14 breaks Django 5.1's test client,
  which errors the template-rendering tests; the image is `python:3.12-slim`,
  where all 29 pass. Runtime rendering is unaffected.
