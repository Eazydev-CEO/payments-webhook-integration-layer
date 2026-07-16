# API Reference

Interactive docs are served at **`/api/docs/`** (Swagger UI) and **`/api/redoc/`**.
The raw OpenAPI schema is at `/api/schema/`.

Management endpoints require an authenticated session (log in at `/login/`).
Webhook receivers are open — their authentication is the processor signature.

## Authentication

| Method | Path | Notes |
|--------|------|-------|
| POST | `/login/` | Session login (username + password) |
| POST | `/logout/` | Session logout |

## Payment intents

### Create (idempotent)
```
POST /api/payment-intents/
Content-Type: application/json

{
  "idempotency_key": "order-1001",
  "processor": "stripe",              // stripe | paystack | manual
  "customer_name": "Ada Lovelace",
  "customer_email": "ada@example.com",
  "amount": "149.99",
  "currency": "USD",
  "metadata": {"order_id": "1001"}
}
```
- `201 Created` on first use of the key.
- `200 OK` with `"idempotent_replay": true` if the key was already used — the
  original intent is returned unchanged.

### List / filter / search
```
GET /api/payment-intents/?status=succeeded&processor__code=stripe&search=ada
```

### Retrieve
```
GET /api/payment-intents/{reference}/
```

### Mark a demo payment
```
POST /api/payment-intents/{reference}/mark/
{ "outcome": "success" }             // success | failed
```

## Webhooks

| Method | Path | Processor |
|--------|------|-----------|
| POST | `/api/webhooks/stripe/` | Stripe (`Stripe-Signature` header) |
| POST | `/api/webhooks/paystack/` | Paystack (`x-paystack-signature` header) |
| POST | `/api/webhooks/internal/` | Internal normalized event |

Response:
```json
{ "received": true, "accepted": true, "duplicate": false,
  "verified": true, "note": "Stripe signature verified", "event_id": "evt_..." }
```
Invalid signatures return `400` with `"verified": false`. See
[WEBHOOKS.md](WEBHOOKS.md).

## Webhook events + retries
```
GET  /api/webhook-events/?status=failed
POST /api/webhook-events/{id}/retry/
```

## CRM deliveries + retries
```
GET  /api/crm-deliveries/?status=permanently_failed&target=hubspot
POST /api/crm-deliveries/{id}/retry/
```

## Settlements

### Import + reconcile
```
POST /api/settlements/import/
Content-Type: multipart/form-data     (or application/json with csv_content)

processor=stripe
reference=stripe-2026-07-08
statement_date=2026-07-08
currency=USD
file=<settlement.csv>
```

### List / retrieve (with reconciliation items)
```
GET /api/settlements/
GET /api/settlements/{id}/
```

## Example: create then simulate settlement (curl)

```bash
# 1. Log in and keep the session cookie + CSRF token
curl -c cookies.txt http://localhost:$APP_PORT/login/

# 2. Create an intent
curl -b cookies.txt -X POST http://localhost:$APP_PORT/api/payment-intents/ \
  -H "Content-Type: application/json" \
  -d '{"idempotency_key":"order-1","processor":"stripe","customer_name":"Ada",
       "customer_email":"ada@example.com","amount":"149.99","currency":"USD"}'
```
