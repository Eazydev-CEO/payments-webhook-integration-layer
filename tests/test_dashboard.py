from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.payments.services import create_payment_intent
from apps.processors.services import ensure_default_processors


class DashboardPageLoadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ensure_default_processors()
        cls.user = get_user_model().objects.create_user(
            username="op", password="pw12345", is_staff=True
        )
        cls.intent = create_payment_intent(
            idempotency_key="d-1", processor_code="stripe",
            customer_name="Ada", customer_email="ada@example.com",
            amount=Decimal("10.00"), currency="USD",
        ).intent

    def setUp(self):
        self.client.force_login(self.user)

    def test_pages_load(self):
        names = [
            "dashboard:overview",
            "dashboard:intents",
            "dashboard:transactions",
            "dashboard:webhooks",
            "dashboard:failed_webhooks",
            "dashboard:crm",
            "dashboard:settlements",
            "dashboard:audit",
            "dashboard:settings",
        ]
        for name in names:
            with self.subTest(page=name):
                resp = self.client.get(reverse(name))
                self.assertEqual(resp.status_code, 200)

    def test_intent_detail_loads(self):
        resp = self.client.get(
            reverse("dashboard:intent_detail", args=[self.intent.reference])
        )
        self.assertEqual(resp.status_code, 200)

    def test_login_required_redirects_anonymous(self):
        self.client.logout()
        resp = self.client.get(reverse("dashboard:overview"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url)

    def test_overview_filter_ranges(self):
        for r in ["today", "7d", "1m", "3m", "all"]:
            resp = self.client.get(reverse("dashboard:overview"), {"range": r})
            self.assertEqual(resp.status_code, 200)


class ApiSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ensure_default_processors()
        cls.user = get_user_model().objects.create_user(
            username="apiuser", password="pw12345"
        )

    def test_create_intent_via_api_is_idempotent(self):
        self.client.force_login(self.user)
        payload = {
            "idempotency_key": "api-1", "processor": "stripe",
            "customer_name": "Ada", "customer_email": "ada@example.com",
            "amount": "42.00", "currency": "USD",
        }
        r1 = self.client.post("/api/payment-intents/", payload, content_type="application/json")
        self.assertEqual(r1.status_code, 201)
        r2 = self.client.post("/api/payment-intents/", payload, content_type="application/json")
        self.assertEqual(r2.status_code, 200)  # idempotent replay
        self.assertTrue(r2.json()["idempotent_replay"])

    def test_webhook_endpoint_open_and_processes(self):
        # No auth required for webhook receiver.
        create_payment_intent(
            idempotency_key="api-wh", processor_code="paystack",
            customer_name="Ada", customer_email="ada@example.com",
            amount=Decimal("30.00"), currency="NGN",
        )
        import json
        body = json.dumps({
            "event": "charge.success",
            "data": {"reference": "nope", "amount": 3000, "currency": "NGN",
                     "customer": {"email": "ada@example.com"}},
        })
        resp = self.client.post(
            "/api/webhooks/paystack/", body, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["received"])
