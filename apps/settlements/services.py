"""
Settlement import + reconciliation.

Reconciliation matches each settlement line's reference to an internal
PaymentIntent and flags:

  * matched            — reference found, amount + currency agree
  * amount_mismatch    — reference found, amount differs
  * currency_mismatch  — reference found, currency differs
  * unknown            — settlement line references nothing we know
  * missing            — a succeeded internal payment absent from the settlement
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_audit
from apps.payments.models import PaymentIntent, PaymentStatus
from apps.processors.models import PaymentProcessor

from .models import MatchStatus, SettlementBatch, SettlementItem

REQUIRED_COLUMNS = {"reference", "amount", "currency", "status"}


class SettlementError(Exception):
    """Raised for malformed settlement input."""


@dataclass
class ParsedRow:
    reference: str
    amount: Decimal
    currency: str
    status: str
    raw: dict = field(default_factory=dict)


def parse_settlement_csv(content: str) -> list[ParsedRow]:
    """Parse CSV text into rows. Expected columns: reference, amount, currency, status[, paid_at]."""
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise SettlementError("CSV file is empty.")
    headers = {h.strip().lower() for h in reader.fieldnames}
    missing = REQUIRED_COLUMNS - headers
    if missing:
        raise SettlementError(f"CSV missing required columns: {', '.join(sorted(missing))}")

    rows: list[ParsedRow] = []
    for i, raw in enumerate(reader, start=2):
        norm = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        ref = norm.get("reference", "")
        if not ref:
            continue
        try:
            amount = Decimal(norm.get("amount", "0") or "0")
        except InvalidOperation as exc:
            raise SettlementError(f"Row {i}: invalid amount '{norm.get('amount')}'.") from exc
        rows.append(
            ParsedRow(
                reference=ref,
                amount=amount,
                currency=(norm.get("currency", "USD") or "USD").upper()[:3],
                status=norm.get("status", ""),
                raw=norm,
            )
        )
    return rows


def _classify(row: ParsedRow, intent: PaymentIntent | None) -> tuple[str, str]:
    if intent is None:
        return MatchStatus.UNKNOWN, "No internal payment matches this reference."
    if row.currency != intent.currency:
        return (
            MatchStatus.CURRENCY_MISMATCH,
            f"Settlement {row.currency} vs internal {intent.currency}.",
        )
    if row.amount != intent.amount:
        return (
            MatchStatus.AMOUNT_MISMATCH,
            f"Settlement {row.amount} vs internal {intent.amount}.",
        )
    return MatchStatus.MATCHED, "Reference, amount and currency agree."


@transaction.atomic
def import_and_reconcile(
    *,
    processor_code: str,
    reference: str,
    statement_date: date,
    csv_content: str,
    currency: str = "USD",
    source_filename: str = "",
    actor=None,
) -> SettlementBatch:
    """Import a settlement CSV and reconcile it in one atomic operation."""
    try:
        processor = PaymentProcessor.objects.get(code=processor_code)
    except PaymentProcessor.DoesNotExist as exc:
        raise SettlementError(f"Unknown processor '{processor_code}'.") from exc

    rows = parse_settlement_csv(csv_content)

    batch = SettlementBatch.objects.create(
        processor=processor,
        reference=reference,
        statement_date=statement_date,
        currency=currency.upper()[:3],
        status=SettlementBatch.Status.PENDING,
        source_filename=source_filename,
        uploaded_by=actor if getattr(actor, "is_authenticated", False) else None,
    )

    reconcile_batch(batch, rows, actor=actor)
    record_audit(
        "settlement_imported",
        f"Imported settlement {batch.reference} ({len(rows)} rows) for {processor_code}",
        actor=actor,
        entity_type="SettlementBatch",
        entity_id=batch.reference,
        metadata={"rows": len(rows)},
    )
    return batch


def reconcile_batch(
    batch: SettlementBatch,
    rows: list[ParsedRow] | None = None,
    *,
    actor=None,
) -> SettlementBatch:
    """(Re)build settlement items for a batch and compute reconciliation totals."""
    if rows is None:
        # Rebuild from stored raw rows on re-reconcile.
        rows = [
            ParsedRow(
                reference=item.external_reference,
                amount=item.amount,
                currency=item.currency,
                status=item.reported_status,
                raw=item.raw,
            )
            for item in batch.items.exclude(match_status=MatchStatus.MISSING)
        ]

    batch.items.all().delete()

    received_total = Decimal("0.00")
    settlement_refs: set[str] = set()

    for row in rows:
        intent = PaymentIntent.objects.filter(reference=row.reference).first()
        match_status, detail = _classify(row, intent)
        SettlementItem.objects.create(
            batch=batch,
            external_reference=row.reference,
            amount=row.amount,
            currency=row.currency,
            reported_status=row.status,
            match_status=match_status,
            matched_intent=intent,
            detail=detail,
            raw=row.raw,
        )
        received_total += row.amount
        settlement_refs.add(row.reference)

    # Missing: succeeded internal payments for this processor absent from the file.
    succeeded_intents = PaymentIntent.objects.filter(
        processor=batch.processor, status=PaymentStatus.SUCCEEDED
    )
    expected_total = Decimal("0.00")
    for intent in succeeded_intents:
        expected_total += intent.amount
        if intent.reference not in settlement_refs:
            SettlementItem.objects.create(
                batch=batch,
                external_reference=intent.reference,
                amount=intent.amount,
                currency=intent.currency,
                reported_status="",
                match_status=MatchStatus.MISSING,
                matched_intent=intent,
                detail="Succeeded internally but not present in settlement file.",
                raw={},
            )

    batch.expected_amount = expected_total
    batch.received_amount = received_total
    batch.difference = received_total - expected_total
    batch.status = SettlementBatch.Status.RECONCILED
    batch.reconciled_at = timezone.now()
    batch.summary = {
        "matched": batch.items.filter(match_status=MatchStatus.MATCHED).count(),
        "amount_mismatch": batch.items.filter(
            match_status=MatchStatus.AMOUNT_MISMATCH
        ).count(),
        "currency_mismatch": batch.items.filter(
            match_status=MatchStatus.CURRENCY_MISMATCH
        ).count(),
        "missing": batch.items.filter(match_status=MatchStatus.MISSING).count(),
        "unknown": batch.items.filter(match_status=MatchStatus.UNKNOWN).count(),
    }
    batch.save()

    record_audit(
        "reconciled",
        f"Reconciled settlement {batch.reference}: diff {batch.difference} {batch.currency}",
        actor=actor,
        entity_type="SettlementBatch",
        entity_id=batch.reference,
        metadata=batch.summary,
    )
    return batch
