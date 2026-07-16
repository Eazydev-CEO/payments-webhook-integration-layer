from decimal import Decimal

from django.test import TestCase, override_settings

from apps.payments.services import create_payment_intent
from apps.processors.services import ensure_default_processors
from apps.webhooks import signatures
from apps.webhooks.models import WebhookEvent
from apps.webhooks.services import ingest_webhook
from apps.webhooks.simulate import build_paystack_event, build_stripe_event

# Dummy signing keys used only to exercise the HMAC paths in these tests.
# Deliberately not shaped like real processor keys.
STRIPE_SECRET = "unit-test-stripe-webhook-secret"
PAYSTACK_SECRET = "unit-test-paystack-secret-key"


class SignatureTests(TestCase):
    def setUp(self):
        ensure_default_processors()

    @override_settings(STRIPE_WEBHOOK_SECRET=STRIPE_SECRET)
    def test_stripe_signature_success(self):
        body, headers = build_stripe_event(
            reference="pi_x", amount=Decimal("10.00"), currency="USD",
            email="a@b.com", succeeded=True,
        )
        result = signatures.verify_stripe(body, headers["stripe-signature"])
        self.assertTrue(result.verified)

    @override_settings(STRIPE_WEBHOOK_SECRET=STRIPE_SECRET)
    def test_stripe_signature_failure(self):
        body, headers = build_stripe_event(
            reference="pi_x", amount=Decimal("10.00"), currency="USD",
            email="a@b.com", succeeded=True,
        )
        # Tamper with the payload after signing.
        result = signatures.verify_stripe(body + "tampered", headers["stripe-signature"])
        self.assertFalse(result.verified)

    @override_settings(PAYSTACK_SECRET_KEY=PAYSTACK_SECRET)
    def test_paystack_signature_success(self):
        body, headers = build_paystack_event(
            reference="pi_y", amount=Decimal("10.00"), currency="NGN",
            email="a@b.com", succeeded=True,
        )
        result = signatures.verify_paystack(body, headers["x-paystack-signature"])
        self.assertTrue(result.verified)

    @override_settings(PAYSTACK_SECRET_KEY=PAYSTACK_SECRET)
    def test_paystack_signature_failure(self):
        body, _ = build_paystack_event(
            reference="pi_y", amount=Decimal("10.00"), currency="NGN",
            email="a@b.com", succeeded=True,
        )
        result = signatures.verify_paystack(body, "deadbeef")
        self.assertFalse(result.verified)


class IngestTests(TestCase):
    def setUp(self):
        ensure_default_processors()
        self.intent = create_payment_intent(
            idempotency_key="wh-1", processor_code="stripe",
            customer_name="Ada", customer_email="ada@example.com",
            amount=Decimal("120.00"), currency="USD",
        ).intent

    @override_settings(STRIPE_WEBHOOK_SECRET=STRIPE_SECRET)
    def test_valid_webhook_processed_and_updates_intent(self):
        body, headers = build_stripe_event(
            reference=self.intent.reference, amount=self.intent.amount,
            currency="USD", email="ada@example.com", succeeded=True,
        )
        result = ingest_webhook(processor_code="stripe", raw_body=body, headers=headers)
        self.assertTrue(result.accepted)
        self.assertTrue(result.verified)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, "succeeded")
        self.assertTrue(self.intent.transactions.exists())

    @override_settings(STRIPE_WEBHOOK_SECRET=STRIPE_SECRET)
    def test_invalid_signature_rejected(self):
        body, headers = build_stripe_event(
            reference=self.intent.reference, amount=self.intent.amount,
            currency="USD", email="ada@example.com", succeeded=True,
        )
        headers["stripe-signature"] = "t=1,v1=bad"
        result = ingest_webhook(processor_code="stripe", raw_body=body, headers=headers)
        self.assertFalse(result.accepted)
        self.assertEqual(result.http_status, 400)
        # Rejected event is still stored for audit.
        self.assertTrue(WebhookEvent.objects.filter(signature_verified=False).exists())

    @override_settings(STRIPE_WEBHOOK_SECRET=STRIPE_SECRET)
    def test_duplicate_webhook_stored_but_ignored(self):
        body, headers = build_stripe_event(
            reference=self.intent.reference, amount=self.intent.amount,
            currency="USD", email="ada@example.com", succeeded=True,
        )
        first = ingest_webhook(processor_code="stripe", raw_body=body, headers=headers)
        second = ingest_webhook(processor_code="stripe", raw_body=body, headers=headers)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        # Two rows stored, exactly one is flagged duplicate.
        self.assertEqual(WebhookEvent.objects.filter(is_duplicate=True).count(), 1)
        self.assertEqual(WebhookEvent.objects.filter(is_duplicate=False).count(), 1)

    def test_demo_mode_accepts_without_secret(self):
        # No secret configured -> demo mode accepts (flagged as verified/demo).
        body, headers = build_stripe_event(
            reference=self.intent.reference, amount=self.intent.amount,
            currency="USD", email="ada@example.com", succeeded=False,
        )
        result = ingest_webhook(processor_code="stripe", raw_body=body, headers=headers)
        self.assertTrue(result.accepted)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, "failed")
