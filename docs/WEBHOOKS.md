# Webhooks

## Endpoints

| Processor | Endpoint | Signature header | Algorithm |
|-----------|----------|------------------|-----------|
| Stripe | `POST /api/webhooks/stripe/` | `Stripe-Signature` | HMAC-SHA256 of `"{t}.{body}"` |
| Paystack | `POST /api/webhooks/paystack/` | `x-paystack-signature` | HMAC-SHA512 of raw body |
| Internal | `POST /api/webhooks/internal/` | — | demo/normalized envelope |

## Signature verification

Implemented in `apps/webhooks/signatures.py`.

**Stripe** — the header is `t=<unix>,v1=<hex>`. We recompute
`HMAC-SHA256(secret, f"{t}.{raw_body}")` and compare in constant time, then
enforce a 5-minute timestamp tolerance (replay protection).

**Paystack** — we recompute `HMAC-SHA512(secret, raw_body)` and compare in
constant time against the header.

### Demo acceptance
Enforcement is decided **per processor, by the presence of that processor's
secret** — not by the `DEMO_MODE` setting. If the relevant secret
(`STRIPE_WEBHOOK_SECRET` / `PAYSTACK_SECRET_KEY`) is **not** configured, that
receiver accepts signatures and clearly flags them (`verified=True, demo=True`).
As soon as the secret is present it is **enforced** — an invalid signature is
rejected with `400` and stored with `signature_verified=False` for audit.

The `DEMO_MODE` setting is a display flag only: it drives the dashboard's demo
banner and gates nothing in this pipeline. Setting `DEMO_MODE=False` does not
start enforcing a signature whose secret is absent. See `../ENVIRONMENT.md`.

## Idempotency

Every event carries an `event_id`. The first occurrence is processed; any
re-delivery of the same id is **stored** (for audit) but flagged
`is_duplicate=True` and never reprocessed. This means an at-least-once
processor can safely retry without causing double charges/updates.

## Normalization

`apps/webhooks/normalize.py` translates each processor's payload into one
internal shape:

```json
{
  "event_id": "evt_123",
  "processor": "stripe",
  "event_type": "payment.succeeded",   // payment.succeeded | payment.failed | payment.updated
  "raw_event_type": "payment_intent.succeeded",
  "reference": "pi_abc",               // links to a PaymentIntent
  "external_id": "ch_...",
  "amount": "149.99",                  // major units (minor→major conversion done here)
  "currency": "USD",
  "customer_email": "ada@example.com",
  "outcome": "succeeded"
}
```

Downstream code (payment updates, CRM fan-out, reconciliation) only ever sees
this shape.

## Testing webhooks locally

From the dashboard **Webhook Events → Simulate webhook**: pick a payment intent
and an outcome; the app builds a realistically-shaped, signed payload for that
intent's processor and posts it through the exact production pipeline.

Or via the API in demo mode:
```bash
curl -X POST http://localhost:$APP_PORT/api/webhooks/paystack/ \
  -H "Content-Type: application/json" \
  -d '{"event":"charge.success",
       "data":{"reference":"<pi_reference>","amount":14999,"currency":"USD",
               "customer":{"email":"ada@example.com"}}}'
```

## Retry / backoff

Processing failures move the event through
`pending → processing → failed → (backoff) → … → permanently_failed`, with
exponential backoff (`RETRY_BASE_SECONDS * 2**retry_count`, capped at
`RETRY_MAX_BACKOFF_SECONDS`). Run the queue with:
```bash
docker compose exec web python manage.py process_webhook_retries [--limit N]
```
`--limit` caps jobs per queue and defaults to 100. Operators can also retry any
failed event from **Failed Webhooks** in the UI.
