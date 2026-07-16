from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.payments.services import create_payment_intent, mark_demo_payment
from apps.processors.services import ensure_default_processors
from apps.settlements.models import MatchStatus
from apps.settlements.services import import_and_reconcile


class SettlementReconciliationTests(TestCase):
    def setUp(self):
        ensure_default_processors()
        self.intent = create_payment_intent(
            idempotency_key="s-1", processor_code="stripe",
            customer_name="Ada", customer_email="ada@example.com",
            amount=Decimal("100.00"), currency="USD",
        ).intent
        mark_demo_payment(intent=self.intent, outcome="success")

    def _reconcile(self, csv_content, reference="batch-1"):
        return import_and_reconcile(
            processor_code="stripe",
            reference=reference,
            statement_date=timezone.now().date(),
            csv_content=csv_content,
            currency="USD",
        )

    def test_exact_match(self):
        csv = f"reference,amount,currency,status\n{self.intent.reference},100.00,USD,success\n"
        batch = self._reconcile(csv)
        item = batch.items.get(external_reference=self.intent.reference)
        self.assertEqual(item.match_status, MatchStatus.MATCHED)
        self.assertEqual(batch.difference, Decimal("0.00"))

    def test_amount_mismatch_detected(self):
        csv = f"reference,amount,currency,status\n{self.intent.reference},150.00,USD,success\n"
        batch = self._reconcile(csv, reference="batch-2")
        item = batch.items.get(external_reference=self.intent.reference)
        self.assertEqual(item.match_status, MatchStatus.AMOUNT_MISMATCH)
        self.assertEqual(batch.difference, Decimal("50.00"))

    def test_currency_mismatch_detected(self):
        csv = f"reference,amount,currency,status\n{self.intent.reference},100.00,EUR,success\n"
        batch = self._reconcile(csv, reference="batch-3")
        item = batch.items.get(external_reference=self.intent.reference)
        self.assertEqual(item.match_status, MatchStatus.CURRENCY_MISMATCH)

    def test_unknown_settlement_record(self):
        csv = "reference,amount,currency,status\nunknown_ref,10.00,USD,success\n"
        batch = self._reconcile(csv, reference="batch-4")
        item = batch.items.get(external_reference="unknown_ref")
        self.assertEqual(item.match_status, MatchStatus.UNKNOWN)

    def test_missing_transaction_flagged(self):
        # Our succeeded intent is absent from the settlement file -> missing.
        csv = "reference,amount,currency,status\nother_ref,10.00,USD,success\n"
        batch = self._reconcile(csv, reference="batch-5")
        missing = batch.items.filter(match_status=MatchStatus.MISSING)
        self.assertTrue(
            missing.filter(external_reference=self.intent.reference).exists()
        )
