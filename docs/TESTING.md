# Testing

The suite lives in `tests/` and covers the full spec.

## Run

**In Docker (recommended — matches the runtime, Python 3.12):**
```bash
docker compose run --rm --entrypoint "" web python manage.py test
```

**Locally (needs Python 3.12 or 3.13 + a virtualenv):**
```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
.venv/Scripts/python manage.py test
```

> Note: Django 5.1's test client is not yet compatible with **Python 3.14**
> (a `copy()` bug in template-context instrumentation). Use Python 3.12/3.13,
> which is what the Docker image ships. Service-layer tests pass on 3.14; only
> the template-rendering tests need 3.12/3.13.

## Coverage

| File | What it verifies |
|------|------------------|
| `tests/test_payments.py` | Intent creation; **idempotency** duplicate prevention; invalid amount; demo mark; no double-mark |
| `tests/test_webhooks.py` | **Stripe** signature success/failure; **Paystack** signature success/failure; valid webhook updates intent; invalid signature rejected + stored; **duplicate event** stored-but-ignored; demo-mode accept |
| `tests/test_retry.py` | **Exponential backoff** growth + cap; failed→retry→permanently_failed transitions; success clears state; manual retry |
| `tests/test_settlements.py` | Exact **match**; **amount mismatch**; **currency mismatch**; **unknown** record; **missing** transaction |
| `tests/test_dashboard.py` | Every dashboard **page loads (200)**; intent detail; login-required redirect; range filters; API idempotency + open webhook endpoint |

Expected result: **29 tests, OK**.

```
Ran 29 tests
OK
```

`--entrypoint ""` skips the migrate/seed/collectstatic entrypoint so the test
runner starts directly.
