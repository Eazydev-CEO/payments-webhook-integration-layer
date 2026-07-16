from decimal import Decimal

from django.test import TestCase

from apps.payments.models import PaymentIntent, PaymentStatus
from apps.payments.services import (
    PaymentError,
    create_payment_intent,
    mark_demo_payment,
)
from apps.processors.services import ensure_default_processors


class PaymentIntentTests(TestCase):
    def setUp(self):
        ensure_default_processors()

    def _create(self, key="order-1", amount="100.00"):
        return create_payment_intent(
            idempotency_key=key,
            processor_code="stripe",
            customer_name="Ada",
            customer_email="ada@example.com",
            amount=amount,
            currency="USD",
        )

    def test_create_payment_intent(self):
        result = self._create()
        self.assertTrue(result.created)
        self.assertEqual(result.intent.status, PaymentStatus.CREATED)
        self.assertEqual(result.intent.amount, Decimal("100.00"))
        self.assertTrue(result.intent.reference.startswith("pi_"))
        self.assertEqual(PaymentIntent.objects.count(), 1)

    def test_idempotency_prevents_duplicates(self):
        first = self._create(key="same-key", amount="50.00")
        second = self._create(key="same-key", amount="999.00")  # different data, same key
        self.assertTrue(first.created)
        self.assertFalse(second.created)  # idempotent replay
        self.assertEqual(first.intent.pk, second.intent.pk)
        self.assertEqual(PaymentIntent.objects.count(), 1)
        # Original data is preserved (the second request does not mutate it).
        self.assertEqual(second.intent.amount, Decimal("50.00"))

    def test_invalid_amount_rejected(self):
        with self.assertRaises(PaymentError):
            self._create(key="bad", amount="0")

    def test_mark_demo_payment_success(self):
        intent = self._create(key="mark-1").intent
        txn = mark_demo_payment(intent=intent, outcome="success")
        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentStatus.SUCCEEDED)
        self.assertEqual(txn.status, "succeeded")

    def test_cannot_mark_terminal_intent_twice(self):
        intent = self._create(key="mark-2").intent
        mark_demo_payment(intent=intent, outcome="success")
        intent.refresh_from_db()
        with self.assertRaises(PaymentError):
            mark_demo_payment(intent=intent, outcome="failed")
