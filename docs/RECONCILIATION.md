# Settlement Reconciliation

Reconciliation compares a processor's **settlement statement** (what they say
they paid you) against your **internal records** (what you believe you
collected), and flags every discrepancy for the operations team.

## Importing a statement

Dashboard → **Settlements → Import CSV**, or `POST /api/settlements/import/`.

A header row is required. `reference`, `amount`, `currency` and `status` are
**required** columns; `paid_at` is optional and simply retained in the row's
`raw` JSON.

```
reference,amount,currency,status,paid_at
pi_abc123,149.99,USD,success,2026-07-08
```

| Column | Required | Meaning |
|--------|----------|---------|
| `reference` | yes | Must match a `PaymentIntent.reference` in the system |
| `amount` | yes | Amount the processor settled |
| `currency` | yes | Settlement currency |
| `status` | yes | Processor-reported status (informational) |
| `paid_at` | no | Settlement date (informational) |

Header names are case- and whitespace-insensitive. Rows without a reference are
skipped, an unparseable amount raises a row-numbered `SettlementError`, and an
empty file is rejected.

A ready-to-edit sample lives at [`sample_settlement.csv`](sample_settlement.csv).
Its five rows are placeholders — named to illustrate a matched, an amount-mismatch,
a currency-mismatch and an unknown row — so replace the references with real ones
from your **Payment Intents** page before importing. (The `seed_demo_data` command
already imports two reconciled batches, so the Reconciliation view is populated out
of the box.)

## Matching logic

`import_and_reconcile()` in `apps/settlements/services.py` classifies each row:

| Flag | Condition |
|------|-----------|
| **matched** | Reference found; amount **and** currency agree |
| **currency_mismatch** | Reference found; currency differs (checked **before** amount) |
| **amount_mismatch** | Reference found; currency agrees, amount differs |
| **unknown** | Settlement row references nothing in our system |
| **missing** | A succeeded internal payment absent from the settlement file |

Currency is compared first, so a row that differs in *both* currency and amount
is flagged `currency_mismatch`, not `amount_mismatch`.

`missing` rows are synthesized after processing the file: we look at every
succeeded `PaymentIntent` for that processor and flag any that the statement
did not include.

## Totals

| Field | Meaning |
|-------|---------|
| **Expected** | Sum of succeeded internal payments for the processor |
| **Received** | Sum of all settlement line amounts |
| **Difference** | `Received − Expected` (0.00 = balanced) |

## The reconciliation view

`/dashboard/settlements/<id>/` shows:
- Expected / Received / Difference / mismatch count KPIs.
- A colour-coded match-breakdown bar (matched / amount / currency / missing / unknown).
- A filterable table of every settlement item with its match status and a
  human-readable explanation, linking back to the matched payment intent.

## Programmatic reconciliation

```python
from apps.settlements.services import import_and_reconcile
batch = import_and_reconcile(
    processor_code="stripe",
    reference="stripe-2026-07-08",
    statement_date=date(2026, 7, 8),
    csv_content=open("settlement.csv").read(),
    currency="USD",
)
print(batch.difference, batch.summary)
```
