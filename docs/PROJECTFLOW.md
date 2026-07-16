# Project Flow

How a payment moves through the integration layer, end to end.

```
                         ┌────────────────────────────────────────────┐
                         │            PayBridge (Django)                │
                         │                                              │
  Client / merchant ─────┼─▶  POST /api/payment-intents/  (idempotent) │
                         │        └─ create_payment_intent()            │
                         │                                              │
  Stripe  ──webhook──────┼─▶  POST /api/webhooks/stripe/               │
  Paystack ─webhook──────┼─▶  POST /api/webhooks/paystack/            │
  Internal ─event────────┼─▶  POST /api/webhooks/internal/            │
                         │        └─ ingest_webhook()                   │
                         │             1. verify signature             │
                         │             2. normalize event              │
                         │             3. idempotency check            │
                         │             4. apply to PaymentIntent        │
                         │             5. fan out to CRMs               │
                         │                                              │
  Operator ──────────────┼─▶  /dashboard/   (metrics, retries, recon)  │
                         └────────────────────────────────────────────┘
```

## 1. Payment intent creation

`create_payment_intent()` (in `apps/payments/services.py`) is **idempotent**:
the caller supplies an `idempotency_key`, and a repeated request with the same
key returns the original intent rather than creating a duplicate. A unique DB
constraint plus an `IntegrityError` fallback makes this safe under concurrency.

## 2. Webhook ingestion

`ingest_webhook()` (in `apps/webhooks/services.py`) is the single entry point
for all processors:

1. **Verify** the signature (`apps/webhooks/signatures.py`). Stripe uses
   `HMAC-SHA256` over `"{timestamp}.{body}"`; Paystack uses `HMAC-SHA512` over
   the raw body. Enforcement is per processor and decided by the presence of
   that processor's secret: absent → accepted and flagged as demo; present →
   always strictly enforced, and a rejected payload is stored and answered `400`.
2. **Normalize** into one internal shape (`apps/webhooks/normalize.py`). This
   runs *before* the idempotency check, because the `event_id` is read off the
   normalized shape.
3. **Idempotency** — a re-delivered event (same `event_id`) is **stored** for
   audit but flagged `is_duplicate` and never reprocessed.
4. **Apply** to the matching `PaymentIntent` and create a `PaymentTransaction`.
5. **Fan out** to CRMs (`apps/crm/services.py`).

An audit entry is written at each stage rather than only at the end:
`webhook_received`, then `webhook_verified` or `webhook_rejected`, then
`webhook_duplicate` or `webhook_processed`.

## 3. Retry / backoff

Both webhook processing and CRM delivery are *retryable jobs*
(`apps/common/retry.py`) that share one lifecycle:

```
pending → processing → success
                    ↘ failed → (backoff) → processing → …
                            ↘ permanently_failed
```

Backoff is exponential: `base * 2**retry_count`, capped. The runner
`manage.py process_webhook_retries` processes everything whose backoff window
has elapsed; operators can also retry manually from the dashboard.

## 4. Settlement reconciliation

`import_and_reconcile()` (in `apps/settlements/services.py`) parses a settlement
CSV and matches each line's reference to an internal `PaymentIntent`, flagging
matched / amount-mismatch / currency-mismatch / unknown / missing rows. See
[RECONCILIATION.md](RECONCILIATION.md).

## Apps

| App | Responsibility |
|-----|----------------|
| `accounts` | Login/logout, admin seeding, auth audit hooks |
| `processors` | `PaymentProcessor` registry (Stripe / Paystack / manual) |
| `payments` | `PaymentIntent`, `PaymentTransaction`, idempotent creation |
| `webhooks` | Ingestion, signatures, normalization, processing, retries |
| `crm` | `CRMDelivery` fan-out with retry/backoff |
| `settlements` | Settlement import + reconciliation |
| `audit` | Immutable `AuditLog` trail |
| `dashboard` | Operator UI, metrics, actions |
| `api` | DRF serializers + views (unified REST API) |
| `common` | Shared retry/backoff primitives |
