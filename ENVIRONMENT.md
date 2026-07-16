# ENVIRONMENT.md — configuration reference

Every deploy-specific knob is an environment variable read in `config/settings.py`.
`python-dotenv` loads `BASE_DIR/.env` at import time; Docker additionally injects
real environment variables, which take precedence over `.env` file contents.

`.env` is gitignored (`.env` and `.env.*`, with `!.env.example` re-included).
`.env.example` is the committed template — copy it to `.env` and fill in your own
values. Every secret-bearing key in the template ships **empty**. **Never commit `.env`.**

```bash
cp .env.example .env
```

Docker supplies configuration two ways (`docker-compose.yml`):

| Mechanism | Supplies |
|---|---|
| `env_file: [.env]` | every variable in this document |
| `environment:` | `DJANGO_SETTINGS_MODULE=config.settings`, `DB_DIR=/data` (overrides `.env`) |

`scripts/run_free_port.ps1` rewrites two keys in `.env` on every run: `APP_PORT`
(the confirmed-free port it selected — appended if the key is absent) and — only
if the key already exists — `CSRF_TRUSTED_ORIGINS`, which it sets to
`http://localhost:<port>,http://127.0.0.1:<port>`. If `.env` does not exist it is
first copied from `.env.example`.

## Value parsing

Two helpers in `config/settings.py` handle boolean and list coercion; numeric keys
use `int(os.getenv(...))` and `DB_DIR` is wrapped in `Path(...)` directly:

| Helper | Behaviour |
|---|---|
| `env_bool(key, default)` | `os.getenv(key, str(default))`, stripped and lowercased, is true **only** if it is one of `1`, `true`, `yes`, `on`. Every other value — `False`, `0`, `off`, `no`, empty string, typos — is false. |
| `env_list(key, default)` | splits on `,`, strips each item, drops empties. |

Two consequences worth knowing:

- Because `env_bool` stringifies the default, an unset boolean falls back to
  `str(default)` and is re-parsed through the same truthy set.
- A key that is **present but empty** (`DEBUG=`) reads as `""`, which is *false* —
  it does **not** fall back to the default. Delete the line to get the default.

Keys read with `os.getenv(key) or "<fallback>"` (`SECRET_KEY`, `ADMIN_USERNAME`,
`ADMIN_EMAIL`) behave differently: an empty value **does** fall through to the
fallback.

## Docker host port

| Variable | Default | Notes |
|---|---|---|
| `APP_PORT` | empty in `.env.example`; compose falls back to `8000` via `${APP_PORT:-8000}` (which also covers an empty value) | Host port only. Never read by `settings.py` — it is consumed by `docker-compose.yml` as `"${APP_PORT}:8000"`. The container **always** listens on 8000. `run_free_port.ps1` picks a confirmed-free random port in `[10000, 60000]` and writes it here, so the host port differs run to run — read the current value from `.env`. |

## Core Django

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | `django-insecure-development-key-do-not-use-in-production` | `os.getenv("SECRET_KEY") or "<insecure development key>"` — unset **or empty** falls back, so the project boots straight from `.env.example`. Not validated at boot: a weak key starts silently. Always set a real one in production. |
| `DEBUG` | `True` (`env_bool("DEBUG", True)`) | Set `False` in production; gates the whole security block below. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,0.0.0.0` | `env_list`, comma-separated. |
| `CSRF_TRUSTED_ORIGINS` | *(empty)* | `env_list`. Needs a scheme, e.g. `http://localhost:<APP_PORT>`. Rewritten by `run_free_port.ps1` when the key is already present in `.env`. |
| `SITE_NAME` | `PayBridge` | Exposed to every template by `apps.dashboard.context_processors.site_context`. |
| `TIME_ZONE` | `UTC` | `USE_TZ = True` is hardcoded; `LANGUAGE_CODE` is not configurable. |

## Database

SQLite only. There is no `DATABASE_URL` and no PostgreSQL support.

| Variable | Default | Notes |
|---|---|---|
| `DB_DIR` | `BASE_DIR` (the project root) | Directory holding `db.sqlite3`. `settings.py` runs `DB_DIR.mkdir(parents=True, exist_ok=True)` at import. |

Under Docker `DB_DIR` is forced to `/data` in **both** the `Dockerfile` (`ENV DB_DIR=/data`)
and the compose `environment:` block, and `/data` is the mount point of the named
volume `app_db`. That is what keeps the database alive across
`docker compose up --build` — the default (`BASE_DIR`) would place the file inside
the container's writable layer and lose it on every rebuild.

## Seed admin

Read by `apps/accounts/management/commands/create_admin.py`, which `seed_demo_data`
invokes. The entrypoint runs `seed_demo_data` on every container start (idempotent).

| Variable | Default | Notes |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | `os.getenv("ADMIN_USERNAME") or "admin"` — empty falls back. |
| `ADMIN_EMAIL` | `admin@example.com` | `os.getenv("ADMIN_EMAIL") or "admin@example.com"` — empty falls back. |
| `ADMIN_PASSWORD` | *(none — empty)* | `os.getenv("ADMIN_PASSWORD", "")`. **No default exists.** Choose your own strong password. |

**The admin account is created only when you set `ADMIN_PASSWORD` yourself.** With
it unset or empty, `create_admin` writes a warning (`ADMIN_USERNAME/ADMIN_PASSWORD
not set; skipping.`) and returns without creating a user — the rest of the seed
still runs, and you will have no account to log in with until you set one.

`create_admin` is idempotent and re-asserts config on every run: it
`get_or_create`s the user, then forces `email`, `is_staff`, `is_superuser` and
**resets the password** to the current `ADMIN_PASSWORD` each time it executes.

To create or rotate the account:

```bash
docker compose run --rm --entrypoint "" web python manage.py create_admin
```

## Processor credentials

All optional. Absent secrets are the normal, fully-runnable state.

| Variable | Default | Notes |
|---|---|---|
| `DEMO_MODE` | derived — see below | `.env.example` ships `DEMO_MODE=True`, which pins it on regardless of the derivation. |
| `STRIPE_SECRET_KEY` | *(empty)* | Presence flips `Processor.is_live` / `mode_label` to `Live` for the Stripe processor. |
| `STRIPE_WEBHOOK_SECRET` | *(empty)* | HMAC-SHA256 key for the `Stripe-Signature: t=<ts>,v1=<hex>` header. When set, `verify_stripe` enforces the signature **and** a 300-second timestamp tolerance. Also the signing key `apps/webhooks/simulate.py` uses for simulated Stripe events (unsigned events carry `t=<ts>,v1=demo`). Does **not** take part in the `DEMO_MODE` derivation. |
| `PAYSTACK_SECRET_KEY` | *(empty)* | Doubles as the HMAC-SHA512 key for `x-paystack-signature` (`verify_paystack` reads this, matching Paystack's real scheme), as the live/demo flag for the Paystack processor, and as the signing key used by `apps/webhooks/simulate.py` to sign simulated Paystack events (unsigned events carry the literal `demo`). |
| `PAYSTACK_WEBHOOK_SECRET` | *(empty)* | Read into settings for symmetry but **not consumed by any code path** — Paystack verification uses `PAYSTACK_SECRET_KEY`. Setting it has no effect. |

### How DEMO_MODE is derived

```python
DEMO_MODE = env_bool("DEMO_MODE", not (STRIPE_SECRET_KEY or PAYSTACK_SECRET_KEY))
```

The default is `True` when **neither** `STRIPE_SECRET_KEY` nor `PAYSTACK_SECRET_KEY`
is set, and `False` as soon as either is non-empty. An explicit `DEMO_MODE` value
overrides the derivation in both directions, subject to the `env_bool` truthy set
(so a present-but-empty `DEMO_MODE=` forces it **off**).

Two behaviours are commonly conflated; they are independent:

| Concern | Actually driven by |
|---|---|
| The demo-mode flag surfaced to templates | the `DEMO_MODE` setting (its only consumer is `apps.dashboard.context_processors.site_context`) |
| Whether a webhook signature is enforced | the **presence of the relevant secret**, per-processor, inside `apps/webhooks/signatures.py` |

So `DEMO_MODE=False` does not start enforcing signatures, and `DEMO_MODE=True` does
not relax them: a **present** secret is always enforced. With the secret absent,
`verify_stripe` / `verify_paystack` return `verified=True, demo=True` and the result
is flagged rather than silently accepted.

## Retry / backoff

Consumed by `apps/common/retry.py` for both webhook processing jobs and CRM deliveries.

| Variable | Default | Notes |
|---|---|---|
| `RETRY_MAX_ATTEMPTS` | `5` | `int`. Becomes the **model field default** for `RetryableJob.max_retries`, so it is baked in at row-creation time — changing it does not retroactively alter existing jobs. At `retry_count >= max_retries` the job becomes `permanently_failed`. |
| `RETRY_BASE_SECONDS` | `30` | `int`. Base of `compute_backoff_seconds`: `base * 2 ** retry_count`. Read at call time. |
| `RETRY_MAX_BACKOFF_SECONDS` | `3600` | `int`. Ceiling applied to the computed delay. |

With the defaults the schedule is 60s, 120s, 240s, 480s (`mark_failure` increments
`retry_count` before scheduling the next attempt), and the fifth failure exhausts
`max_retries` and marks the job `permanently_failed`. Note that the default ceiling
is never reached: with `RETRY_MAX_ATTEMPTS=5` the longest delay is 480s, well under
`RETRY_MAX_BACKOFF_SECONDS=3600`. Raise `RETRY_MAX_ATTEMPTS` (or
`RETRY_BASE_SECONDS`) before the cap becomes load-bearing.

## Logging

| Variable | Default | Notes |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Sets the root logger level; single `StreamHandler` to console. Passed through verbatim — an invalid level raises at configuration time. |

## Production-only toggles

`settings.py` applies this block **only when `DEBUG` is false**. Under `DEBUG=True`
these variables are never read and the unconditional defaults below still apply.

Unconditional, regardless of `DEBUG`:

| Setting | Value | Notes |
|---|---|---|
| `SESSION_COOKIE_HTTPONLY` | `True` | |
| `CSRF_COOKIE_HTTPONLY` | `False` | deliberate — dashboard JS reads the token from the cookie for `fetch` |
| `X_FRAME_OPTIONS` | `DENY` | |
| `SECURE_CONTENT_TYPE_NOSNIFF` | `True` | |

Applied when `DEBUG=False`:

| Setting | Value | Configurable? |
|---|---|---|
| `SESSION_COOKIE_SECURE` | `True` | no — set automatically |
| `CSRF_COOKIE_SECURE` | `True` | no — set automatically |
| `SECURE_PROXY_SSL_HEADER` | `("HTTP_X_FORWARDED_PROTO", "https")` | no — set automatically |
| `SECURE_SSL_REDIRECT` | `env_bool("SECURE_SSL_REDIRECT", False)` → `False` | yes — enable once TLS terminates in front |
| `SECURE_HSTS_SECONDS` | `int(os.getenv("SECURE_HSTS_SECONDS", "0"))` → `0` (off) | yes — e.g. `31536000` |

`SECURE_HSTS_INCLUDE_SUBDOMAINS` and `SECURE_HSTS_PRELOAD` are not set by this
project.

## Generate a SECRET_KEY

```bash
docker compose run --rm --entrypoint "" web \
  python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Paste the output into `SECRET_KEY=` in `.env`.

## Production checklist

- [ ] `SECRET_KEY` set to a generated value — never left empty, which silently
      selects the insecure development key.
- [ ] `DEBUG=False` — this is what activates the secure-cookie block.
- [ ] `ALLOWED_HOSTS` set to the real hostname(s).
- [ ] `CSRF_TRUSTED_ORIGINS` set with scheme for every origin serving the dashboard.
- [ ] `ADMIN_PASSWORD` set to a strong password of your own (there is no default,
      and no admin account exists until you set it); `ADMIN_USERNAME` / `ADMIN_EMAIL` reviewed.
- [ ] Real `STRIPE_SECRET_KEY` and/or `PAYSTACK_SECRET_KEY` set — these two alone flip
      derived `DEMO_MODE` to false — and confirm `DEMO_MODE` is not pinned `True` in
      `.env` (the `.env.example` template ships it as `True`).
- [ ] `STRIPE_WEBHOOK_SECRET` set if you receive Stripe webhooks: it does not affect
      `DEMO_MODE`, but without it Stripe signatures are never enforced.
- [ ] `SECURE_SSL_REDIRECT=True` and `SECURE_HSTS_SECONDS` set once TLS is terminating.
- [ ] `DB_DIR` pointing at a persistent volume (`/data` under the supplied compose file).
- [ ] `LOG_LEVEL` reviewed (`INFO` or `WARNING`).
- [ ] `.env` confirmed absent from version control.

Related: [README.md](README.md) (quick start), [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
(Docker + port selection), [docs/SECURITY.md](docs/SECURITY.md) (signature verification,
auth), [docs/WEBHOOKS.md](docs/WEBHOOKS.md) (retry pipeline).
