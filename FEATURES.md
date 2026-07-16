# FEATURES.md — complete feature list

A section-by-section inventory of what this system actually does, derived from the source. Deep detail lives in
the linked documents under `docs/`; this file is the map.

---

## 1. Authentication & access

- Django **session authentication** only. No JWT, no API keys, no RBAC/roles — a single admin/operator account.
- Routes: `/login/` and `/logout/` (`apps/accounts/urls.py`), thin subclasses of Django's built-in auth views
  (`apps/accounts/views.py`). `/` redirects to `dashboard:overview`.
- `LOGIN_URL = accounts:login`, `LOGIN_REDIRECT_URL = dashboard:overview`, `LOGOUT_REDIRECT_URL = accounts:login`.
- Every dashboard view is `@login_required`; mutating views add `@require_POST` and are CSRF-protected.
- Login and logout are audit-logged with client IP via `user_logged_in` / `user_logged_out` receivers
  (`apps/accounts/signals.py`).
- The admin account is created **only when `ADMIN_PASSWORD` is set** in the environment. `create_admin` skips with a
  warning when `ADMIN_USERNAME` or `ADMIN_PASSWORD` is empty. `ADMIN_USERNAME` defaults to `admin`, `ADMIN_EMAIL` to
  `admin@example.com`; there is no password default. The command is idempotent (`get_or_create`, then realigns
  email/staff/superuser/password from env).
- Django's four default password validators are enabled. `SESSION_COOKIE_HTTPONLY = True`; `CSRF_COOKIE_HTTPONLY`
  is deliberately `False` so dashboard JS can read the token for `fetch`. `X_FRAME_OPTIONS = DENY`,
  `SECURE_CONTENT_TYPE_NOSNIFF = True`. When `DEBUG` is off, secure session/CSRF cookies and the proxy SSL header
  are enabled automatically.

See [docs/SECURITY.md](docs/SECURITY.md).

## 2. Payment processors

- `PaymentProcessor` (`apps/processors/models.py`) with unique `code` from `ProcessorCode`:
  `stripe`, `paystack`, `manual` ("Manual / Internal Demo").
- Fields: `code`, `name`, `is_active`, `supports_webhooks`, `config` (non-secret display config only — secrets stay
  in env vars), `created_at`, `updated_at`.
- `is_live` reports whether real credentials are configured (`STRIPE_SECRET_KEY` / `PAYSTACK_SECRET_KEY`); `manual`
  is never live. `mode_label` renders `Live` or `Demo` for the dashboard.
- **DEMO_MODE** defaults to `not (STRIPE_SECRET_KEY or PAYSTACK_SECRET_KEY)` — automatically on when no processor
  secret is present, so the project runs with no external account. A *present* secret is always strictly enforced.

## 3. Payment intents & transactions

- `PaymentIntent` (`apps/payments/models.py`): `reference` (unique, auto `pi_<hex>`), `idempotency_key` (unique,
  indexed), `processor` (FK, `PROTECT`), `customer_name`, `customer_email`, `amount` (14,2), `currency` (3 chars,
  default `USD`), `status`, `metadata` (JSON), `created_at`, `updated_at`.
- `PaymentStatus`: `created`, `processing`, `succeeded`, `failed`, `canceled`. `is_terminal` covers the last three.
- `PaymentTransaction`: concrete money movement attached to an intent — `intent`, `processor`, `reference`,
  `external_id`, `amount`, `currency`, `status`, `raw_response` (JSON), `created_at`.
- `TransactionStatus`: `pending`, `succeeded`, `failed`, `refunded`.
- Creation validates the amount as a `Decimal` > 0 and resolves an **active** processor, else `PaymentError` → HTTP 400.
- `mark_demo_payment` lets an operator settle a demo intent (`success` / `failed`), writing a `PaymentTransaction`
  atomically and rejecting intents already in a terminal state.
- `PaymentIntent` carries composite indexes on `(status, created_at)` and `(processor, status)`;
  `PaymentTransaction` indexes `(status, created_at)` and `reference`.

## 4. Idempotency (both layers)

**Payment intents.** `idempotency_key` is unique and indexed. A repeat request short-circuits and returns the
**original** intent with `"idempotent_replay": true` and **HTTP 200** (**201** only on first create). The replay path
records an `intent_duplicate` audit entry. A concurrent request that loses the unique-key race is caught via
`IntegrityError` and also returns the winner's intent (that path returns directly, without a second audit row).

**Webhooks.** A re-delivered `event_id` is **stored for audit** but flagged `is_duplicate=True`, given status
`success` (nothing to do) and **never reprocessed** — `process_webhook_event` returns early on duplicates. There is
deliberately **no DB unique constraint on `(processor, event_id)`** so duplicates remain storable; idempotency is
enforced in the service layer by looking up the first non-duplicate event. An index covers `(processor, event_id)`.

## 5. Webhook security

Implemented in `apps/webhooks/signatures.py`; returns a `VerificationResult(verified, note, demo)`.

- **Stripe** — header `Stripe-Signature: t=<unix>,v1=<hex>`. HMAC-SHA256 over `"{t}.{raw_body}"` keyed with
  `STRIPE_WEBHOOK_SECRET`, compared with `hmac.compare_digest` against every `v1` value present. Replay protection:
  the timestamp must be within a **300-second (5 minute)** tolerance. Distinct rejection notes for a missing header,
  a malformed header, a signature mismatch, an out-of-tolerance timestamp, and an invalid timestamp.
- **Paystack** — header `x-paystack-signature`. HMAC-SHA512 over the raw body keyed with `PAYSTACK_SECRET_KEY`,
  compared with `hmac.compare_digest`.
- **Internal/manual** — accepted and flagged as the internal demo processor.
- **Demo mode is per-secret**: verification returns `verified=True, demo=True` only when that processor's secret is
  **absent**. A configured secret is always enforced.
- **Rejected-but-stored**: a failed verification still creates a `WebhookEvent` with the raw payload and headers,
  `signature_verified=False`, the `verification_note`, status `permanently_failed`, and responds **HTTP 400**. The
  payload is never processed.
- Verification runs against the **raw request body** before JSON parsing. Malformed JSON degrades to `{}` rather
  than raising. Webhook receivers are `AllowAny` + `csrf_exempt` because the signature *is* the authentication.

See [docs/WEBHOOKS.md](docs/WEBHOOKS.md).

## 6. Event normalization

`apps/webhooks/normalize.py` translates every dialect into one internal shape.

- Internal event types: `payment.succeeded`, `payment.failed`, `payment.updated` (the fallback for unmapped types).
- **Stripe map**: `payment_intent.succeeded` → `payment.succeeded`, `charge.succeeded` → `payment.succeeded`,
  `payment_intent.payment_failed` → `payment.failed`, `charge.failed` → `payment.failed`.
- **Paystack map**: `charge.success` → `payment.succeeded`, `charge.failed` → `payment.failed`.
- **Manual**: a `type` already starting with `payment.` passes through; anything else becomes `payment.updated`.
- Normalized shape: `event_id`, `processor`, `event_type`, `raw_event_type`, `reference`, `external_id`, `amount`,
  `currency`, `customer_email`, `outcome` (`succeeded` / `failed` / `pending`).
- **Minor → major units**: Stripe/Paystack amounts (cents/kobo) are divided by 100 and quantized to `0.01`, with
  defensive fallback to `0.00`. Currency is upper-cased and truncated to 3 chars (Stripe default `usd`, Paystack
  default `NGN`).
- Event id extraction: Stripe uses top-level `id`; Paystack has no event id, so a stable one is derived as
  `ps_<raw_type>_<reference|id>`; manual uses its own `event_id` envelope. An empty id falls back to a generated one.
- Applying a normalized event updates the matching intent's status, writes a `PaymentTransaction`, and fans out to
  the CRM layer.

## 7. Retry & backoff

`apps/common/retry.py` — `RetryableJob`, an abstract base extended by **both** `WebhookEvent` and `CRMDelivery`.

- **Five statuses** (`DeliveryStatus`): `pending`, `processing`, `success`, `failed` ("Failed (will retry)"),
  `permanently_failed`.
- Carried fields: `status`, `retry_count`, `max_retries` (defaults to `RETRY_MAX_ATTEMPTS`), `next_retry_at`,
  `last_error` (truncated to 2000 chars), `last_attempt_at`.
- **Formula**: `delay = RETRY_BASE_SECONDS * 2**retry_count`, capped at `RETRY_MAX_BACKOFF_SECONDS`. Defaults are
  base 30s, cap 3600s, 5 attempts.
- `mark_failure` increments `retry_count` and either schedules `next_retry_at` (status `failed`) or gives up with
  `permanently_failed` once `retry_count >= max_retries`. `is_due` is true for `pending`/`failed` jobs whose
  `next_retry_at` is null or elapsed.
- **Runner**: `python manage.py process_webhook_retries [--limit N]` (default 100 per queue) drains due webhook
  events and due CRM deliveries, printing processed/succeeded/failed per queue. Intended for cron.
- **Attempt trail**: `WebhookDeliveryAttempt` records one row per processing attempt — `event`, `attempt_number`,
  `result` (`success` / `failed`), `detail`, `created_at`.
- **Manual retry**: `reset_for_manual_retry` makes a job immediately eligible (no-op for `success`/`processing`
  jobs), exposed from the dashboard and the API.

## 8. CRM delivery layer

- `CRMTarget`: `hubspot` ("HubSpot (demo)"), `zoho` ("Zoho (demo)"), `internal` ("Internal CRM (demo)"). Every
  normalized event is fanned out to all three (`DEFAULT_TARGETS`).
- `CRMDelivery` fields: `target`, `payment_intent` (nullable FK, `SET_NULL`), `source_event_id` (string link back to
  the webhook event), `event_type`, `payload` (JSON), `response` (JSON), `created_at`, `updated_at`, plus all
  `RetryableJob` retry state.
- **Enqueue is idempotent per event+target**: `get_or_create` on `(target, source_event_id)`.
- The call is **simulated**, not a real HTTP request. `_simulate_crm_call` is deterministic: a SHA-256 digest of
  `"{target}:{source_event_id}"` makes a stable ~30% slice fail on the **first** attempt only (HTTP 503, "CRM
  timeout"), so the retry system visibly recovers them. Successful responses carry a `crm_ref`.
- Manual retry via `retry_crm_delivery`; scheduled retries via `process_due_crm_deliveries`. Both outcomes are
  audit-logged (`crm_delivered` / `crm_failed` / `crm_retried`).
- Indexes on `(status, next_retry_at)` and `(target, status)`.

## 9. Settlements & reconciliation

- **CSV columns**: `reference`, `amount`, `currency`, `status` are required; `paid_at` is accepted and retained in
  the row's `raw` JSON. Headers are case- and whitespace-insensitive; rows without a reference are skipped; an
  invalid amount raises a row-numbered `SettlementError`; an empty file is rejected. Sample: `docs/sample_settlement.csv`.
- **Five match flags** (`MatchStatus`): `matched`, `amount_mismatch`, `currency_mismatch`, `missing` ("Missing from
  settlement"), `unknown` ("Unknown settlement record"). Currency is checked before amount; `missing` rows are
  synthesized for succeeded internal payments on that processor absent from the file.
- `SettlementBatch`: `processor`, `reference` (unique), `statement_date`, `period_start`, `period_end`, `currency`,
  `expected_amount`, `received_amount`, `difference`, `status` (`pending` / `reconciled`), `source_filename`,
  `uploaded_by`, `summary` (JSON), `notes`, `created_at`, `reconciled_at`. Helpers: `is_balanced`, `mismatch_count`,
  `count(match_status)`.
- **Totals**: `expected_amount` = sum of succeeded internal intents for the processor; `received_amount` = sum of
  settlement lines; `difference` = received − expected. The `summary` JSON carries a count per match flag.
- `SettlementItem`: `batch`, `external_reference`, `amount`, `currency`, `reported_status`, `match_status`,
  `matched_intent` (nullable), `detail` (human-readable reason), `raw`.
- Import + reconcile is one atomic operation; re-reconciling rebuilds items from stored raw rows.

See [docs/RECONCILIATION.md](docs/RECONCILIATION.md).

## 10. Operator dashboard

Pages (`apps/dashboard/urls.py`): overview `/dashboard/`, intents (+ detail, create, mark), transactions, webhooks,
failed webhooks `/dashboard/webhooks/failed/`, CRM, settlements (+ import, reconciliation `/settlements/<id>/`),
audit, settings.

- **KPI set** (`overview_metrics`): `total_volume`, `successful_payments`, `failed_payments`, `pending_payments`,
  `total_intents`, `duplicate_webhooks`, `failed_webhook_deliveries`, `successful_crm`, `failed_crm`,
  `settlement_mismatches`, `verified_webhooks`, `rejected_webhooks`.
- **Date windows**: `Today` (from midnight UTC), `7D` / `1M` / `3M` (7, 30, 90 days back), `All`
  (unbounded), plus a **custom** `start`/`end` range (`YYYY-MM-DD`, end inclusive to 23:59:59). Default is `7d`.
  Each model is filtered on its own timestamp — intents/CRM on `created_at`, webhooks on `received_at`, settlement
  items on `batch__created_at`.
- **Volume chart**: succeeded-volume series (`volume_series`) bucketed across the selected window and rendered with
  Chart.js. The step is `max(span_days // 14, 1)` days, so short windows are day-by-day and long windows widen the
  bucket toward a ~14-point series.
- **Filters / search / pagination**: intents filter by status, processor and a `q` search across reference and
  customer email; webhooks by processor, status and event id; transactions by status; CRM by target and status;
  audit by action; reconciliation by match flag. Tables paginate at 20 rows (audit at 30).
- **Simulate webhook**: builds a realistically-shaped, correctly-signed Stripe/Paystack/manual payload for a chosen
  intent and outcome (`apps/webhooks/simulate.py`), then pushes it through the real ingestion path — including
  duplicate detection, which reports "stored and ignored".
- **Retry actions**: one-click retry for a webhook event or a CRM delivery, returning to the referring page with a
  success/warning message.
- **Dark/light theme**: toggle persisted to `localStorage` under `pb-theme` and applied via `data-theme` on the root
  element by `static/js/theme-init.js` **before first paint** to avoid a flash. Dark is the default.
- **Ultrawide safety**: the shell is capped at `width: min(100% - 32px, 1440px)` so the layout never stretches on
  very wide displays. **Responsive** breakpoints at 1100px, 900px and 560px.
- Icons via Bootstrap Icons (CDN), charts via Chart.js (CDN).

Screenshots: [`docs/screenshots/`](docs/screenshots/).

## 11. Audit log

`AuditLog` (`apps/audit/models.py`) — an immutable record of security- or money-relevant actions.

- **Actions**: `intent_created`, `intent_duplicate`, `payment_marked`, `webhook_received`, `webhook_verified`,
  `webhook_rejected`, `webhook_duplicate`, `webhook_processed`, `webhook_retried`, `crm_delivered`, `crm_failed`,
  `crm_retried`, `settlement_imported`, `reconciled`, `login`, `logout`.
- **Fields**: `actor` (nullable FK, `SET_NULL` — anonymous actors are stored as null), `action`, `entity_type`,
  `entity_id`, `summary` (truncated to 255), `metadata` (JSON), `ip_address`, `created_at`.
- `client_ip` extracts the client address, honouring a single `X-Forwarded-For` proxy hop.
- **Read-only in Django admin**: every field is in `readonly_fields` and `has_add_permission` returns `False`.
  Filterable by action and date, searchable by summary/entity.
- Indexes on `(action, created_at)` and `(entity_type, entity_id)`.

## 12. REST API & OpenAPI

Mounted under `/api/` (`apps/api_urls.py`). Docs at `/api/docs/` (Swagger), `/api/redoc/`, schema at `/api/schema/`.

| Endpoint | Methods | Permission |
| --- | --- | --- |
| `/api/payment-intents/` | `GET`, `POST` | `IsAuthenticated` |
| `/api/payment-intents/<reference>/` | `GET` | `IsAuthenticated` |
| `/api/payment-intents/<reference>/mark/` | `POST` | `IsAuthenticated` |
| `/api/webhook-events/` | `GET` | `IsAuthenticated` |
| `/api/webhook-events/<pk>/` | `GET` | `IsAuthenticated` |
| `/api/webhook-events/<pk>/retry/` | `POST` | `IsAuthenticated` |
| `/api/crm-deliveries/` | `GET` | `IsAuthenticated` |
| `/api/crm-deliveries/<pk>/` | `GET` | `IsAuthenticated` |
| `/api/crm-deliveries/<pk>/retry/` | `POST` | `IsAuthenticated` |
| `/api/settlements/` | `GET` | `IsAuthenticated` |
| `/api/settlements/<pk>/` | `GET` | `IsAuthenticated` |
| `/api/settlements/import/` | `POST` | `IsAuthenticated` |
| `/api/webhooks/stripe/` | `POST` | `AllowAny` (signature-authenticated) |
| `/api/webhooks/paystack/` | `POST` | `AllowAny` (signature-authenticated) |
| `/api/webhooks/internal/` | `POST` | `AllowAny` (signature-authenticated) |

- Resources are list/retrieve only apart from intent creation; there is no update or delete. Every other state
  change goes through an explicit action endpoint (`mark`, `retry`, `import`).
- Session authentication; page-number pagination at 25; django-filter + search + ordering backends enabled.
  Intents filter on `status`, `currency`, `processor__code` and search reference/name/email/idempotency key,
  ordered by `created_at` or `amount`. Webhook events filter on `status`, `processor__code`, `signature_verified`,
  `is_duplicate`. CRM deliveries filter on `status`, `target`, `event_type`. Settlements filter on `status`,
  `processor__code`, `currency`.
- Settlement import accepts either a multipart `file` or inline `csv_content`; at least one is required.
- Schema generated by drf-spectacular ("Payments & Webhook Integration Layer API", version 1.0.0), with a worked
  create-intent example. Retry endpoints add a `retry_succeeded` boolean to the response.

See [docs/API.md](docs/API.md).

## 13. Demo data seeding

- `python manage.py seed_demo_data [--fresh]` — **idempotent**: re-running does nothing unless `--fresh` wipes the
  seeded data first. It calls `create_admin` (which itself no-ops without `ADMIN_PASSWORD`).
- Produces **32 payment intents, 26 webhook events, 72 CRM deliveries, 2 settlement batches, 191 audit logs** —
  including duplicate webhooks, rejected signatures, failed CRM deliveries mid-backoff and settlement mismatches, so
  every code path is visible on a fresh install.
- Run automatically by the Docker entrypoint.

## 14. Deployment & operations

- **Docker-first.** `python:3.12-slim` base, gunicorn with **3 workers** on port **8000**, 60s timeout.
- **Random host port.** `scripts/run_free_port.ps1` (Windows PowerShell) picks and confirms a free port in
  `[10000, 60000]`, writes `APP_PORT` and `CSRF_TRUSTED_ORIGINS` into `.env`, and runs `docker compose up --build -d`.
  Compose maps `${APP_PORT}:8000`; the container always listens on 8000. The script never stops or kills a process.
- **Persistence.** SQLite lives on the named Docker volume `app_db` mounted at `/data` (`DB_DIR=/data`), so the
  database survives rebuilds.
- **Entrypoint** (`docker-entrypoint.sh`): `migrate` → `seed_demo_data` (non-fatal) → `collectstatic` → gunicorn.
- **Static files** served by WhiteNoise with `CompressedStaticFilesStorage`.
- **Config** is entirely environment-driven; see `.env.example` and `ENVIRONMENT.md` for every variable.
  `SECRET_KEY` falls back to an obviously-insecure development key when unset — set a real one in production.
- **Tests**: 29 tests, run with
  `docker compose run --rm --entrypoint "" web python manage.py test`.
  Django 5.1's test client is incompatible with Python 3.14, so run the suite in Docker.
- **Stack**: Python, Django 5.1, DRF, drf-spectacular, django-filter, WhiteNoise, gunicorn, SQLite, Docker, HTML/CSS,
  Bootstrap Icons, Chart.js. No Celery — retries are driven by a management command on a schedule.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/TESTING.md](docs/TESTING.md).

---

Related: [docs/PROJECTFLOW.md](docs/PROJECTFLOW.md) · [docs/API.md](docs/API.md) ·
[docs/WEBHOOKS.md](docs/WEBHOOKS.md) · [docs/RECONCILIATION.md](docs/RECONCILIATION.md) ·
[docs/SECURITY.md](docs/SECURITY.md) · [docs/TESTING.md](docs/TESTING.md) ·
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
