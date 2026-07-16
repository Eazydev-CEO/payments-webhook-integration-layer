"""
Seed a realistic, populated demo dataset by driving the real service layer
(so idempotency, signature verification, retry/backoff, CRM fan-out and audit
logs are all produced organically).

    python manage.py seed_demo_data [--fresh]

Idempotent: re-running does nothing unless --fresh is passed.
"""
from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.crm.models import CRMDelivery
from apps.payments.models import PaymentIntent, PaymentStatus, PaymentTransaction
from apps.processors.models import ProcessorCode
from apps.processors.services import ensure_default_processors
from apps.payments.services import create_payment_intent, mark_demo_payment
from apps.settlements.models import SettlementBatch, SettlementItem
from apps.settlements.services import import_and_reconcile
from apps.webhooks.models import WebhookDeliveryAttempt, WebhookEvent
from apps.webhooks.services import ingest_webhook
from apps.webhooks.simulate import build_signed_event

SEED_PREFIX = "seed-"

CUSTOMERS = [
    ("Ada Lovelace", "ada@example.com"),
    ("Grace Hopper", "grace@example.com"),
    ("Alan Turing", "alan@example.com"),
    ("Katherine Johnson", "katherine@example.com"),
    ("Linus Torvalds", "linus@example.com"),
    ("Margaret Hamilton", "margaret@example.com"),
    ("Dennis Ritchie", "dennis@example.com"),
    ("Barbara Liskov", "barbara@example.com"),
    ("Tim Berners-Lee", "tim@example.com"),
    ("Radia Perlman", "radia@example.com"),
    ("Guido van Rossum", "guido@example.com"),
    ("Anita Borg", "anita@example.com"),
]
CURRENCIES = {
    ProcessorCode.STRIPE: "USD",
    ProcessorCode.PAYSTACK: "NGN",
    ProcessorCode.MANUAL: "USD",
}


class Command(BaseCommand):
    help = "Seed realistic demo data for the dashboard."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--fresh", action="store_true", help="Wipe seeded demo data first.")

    def handle(self, *args, **options) -> None:
        random.seed(20260708)  # deterministic dataset

        ensure_default_processors()
        call_command("create_admin")

        if options["fresh"]:
            self._wipe()

        if PaymentIntent.objects.filter(idempotency_key__startswith=SEED_PREFIX).exists():
            self.stdout.write(self.style.WARNING("Demo data already present; skipping (use --fresh to reset)."))
            return

        with transaction.atomic():
            intents = self._seed_intents()
        self._seed_webhooks(intents)
        self._seed_settlements()
        self._backdate()

        self.stdout.write(self.style.SUCCESS(
            f"Seeded: {PaymentIntent.objects.count()} intents, "
            f"{WebhookEvent.objects.count()} webhooks, "
            f"{CRMDelivery.objects.count()} CRM deliveries, "
            f"{SettlementBatch.objects.count()} settlements, "
            f"{AuditLog.objects.count()} audit logs."
        ))

    # ------------------------------------------------------------------
    def _wipe(self) -> None:
        SettlementItem.objects.all().delete()
        SettlementBatch.objects.all().delete()
        WebhookDeliveryAttempt.objects.all().delete()
        WebhookEvent.objects.all().delete()
        CRMDelivery.objects.all().delete()
        PaymentTransaction.objects.all().delete()
        PaymentIntent.objects.filter(idempotency_key__startswith=SEED_PREFIX).delete()
        AuditLog.objects.all().delete()
        self.stdout.write(self.style.WARNING("Wiped existing demo data."))

    def _seed_intents(self) -> list[PaymentIntent]:
        processors = [ProcessorCode.STRIPE, ProcessorCode.PAYSTACK, ProcessorCode.MANUAL]
        intents: list[PaymentIntent] = []
        for n in range(1, 33):
            proc = random.choice(processors)
            name, email = random.choice(CUSTOMERS)
            amount = Decimal(random.choice([19.99, 49.00, 99.50, 149.99, 249.00, 500.00, 1200.00]))
            result = create_payment_intent(
                idempotency_key=f"{SEED_PREFIX}order-{n:04d}",
                processor_code=proc,
                customer_name=name,
                customer_email=email,
                amount=amount,
                currency=CURRENCIES[proc],
                metadata={"order_id": 1000 + n, "channel": random.choice(["web", "mobile", "api"])},
            )
            intents.append(result.intent)

        # Demonstrate idempotency: repeat two keys (no duplicates created).
        for n in (1, 2):
            create_payment_intent(
                idempotency_key=f"{SEED_PREFIX}order-{n:04d}",
                processor_code=ProcessorCode.STRIPE,
                customer_name="Duplicate Attempt",
                customer_email="dupe@example.com",
                amount=Decimal("1.00"),
                currency="USD",
            )
        return intents

    def _seed_webhooks(self, intents: list[PaymentIntent]) -> None:
        # Drive ~70% of intents to a terminal state via signed webhooks.
        for intent in intents:
            roll = random.random()
            if roll < 0.15:
                continue  # leave pending
            succeeded = roll < 0.80  # ~65% success, ~20% failure among processed

            if intent.processor.code == ProcessorCode.MANUAL and random.random() < 0.5:
                # Some manual intents settled directly by an operator.
                mark_demo_payment(intent=intent, outcome="success" if succeeded else "failed")
                continue

            raw_body, headers = build_signed_event(
                intent.processor.code,
                reference=intent.reference,
                amount=intent.amount,
                currency=intent.currency,
                email=intent.customer_email,
                succeeded=succeeded,
            )
            ingest_webhook(
                processor_code=intent.processor.code,
                raw_body=raw_body,
                headers=headers,
            )
            # Re-deliver a few events to demonstrate duplicate handling.
            if random.random() < 0.18:
                ingest_webhook(
                    processor_code=intent.processor.code,
                    raw_body=raw_body,
                    headers=headers,
                )

        # A couple of rejected (bad-signature) webhooks — only meaningful with a
        # real secret configured; in demo mode they still exercise the path.
        self.stdout.write("  webhooks + CRM fan-out complete")

    def _seed_settlements(self) -> None:
        # Build a settlement CSV per processor from succeeded intents, with a
        # couple of deliberate discrepancies to populate the reconciliation view.
        today = timezone.now().date()
        for code in (ProcessorCode.STRIPE, ProcessorCode.PAYSTACK):
            succeeded = list(
                PaymentIntent.objects.filter(
                    processor__code=code, status=PaymentStatus.SUCCEEDED
                )[:8]
            )
            if not succeeded:
                continue
            lines = ["reference,amount,currency,status,paid_at"]
            for idx, intent in enumerate(succeeded):
                amount = intent.amount
                currency = intent.currency
                # Introduce discrepancies on a couple of rows.
                if idx == 0:
                    amount = amount + Decimal("5.00")          # amount mismatch
                elif idx == 1:
                    currency = "EUR"                            # currency mismatch
                lines.append(f"{intent.reference},{amount},{currency},success,{today}")
            # An unknown settlement record not present internally.
            lines.append(f"unknown_ref_{code},77.00,USD,success,{today}")
            csv_content = "\n".join(lines)

            import_and_reconcile(
                processor_code=code,
                reference=f"{code}-{today.isoformat()}",
                statement_date=today,
                csv_content=csv_content,
                currency="USD" if code == ProcessorCode.STRIPE else "NGN",
                source_filename=f"{code}_settlement.csv",
            )
        self.stdout.write("  settlements imported + reconciled")

    def _backdate(self) -> None:
        """Spread created_at across ~30 days for a realistic chart."""
        now = timezone.now()
        for intent in PaymentIntent.objects.all():
            delta = timedelta(days=random.randint(0, 29), hours=random.randint(0, 23))
            PaymentIntent.objects.filter(pk=intent.pk).update(created_at=now - delta)
