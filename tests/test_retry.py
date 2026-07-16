from django.test import TestCase, override_settings

from apps.common.retry import DeliveryStatus, compute_backoff_seconds
from apps.crm.models import CRMDelivery, CRMTarget


class BackoffTests(TestCase):
    @override_settings(RETRY_BASE_SECONDS=30, RETRY_MAX_BACKOFF_SECONDS=3600)
    def test_exponential_backoff_growth(self):
        self.assertEqual(compute_backoff_seconds(0), 30)
        self.assertEqual(compute_backoff_seconds(1), 60)
        self.assertEqual(compute_backoff_seconds(2), 120)
        self.assertEqual(compute_backoff_seconds(3), 240)

    @override_settings(RETRY_BASE_SECONDS=30, RETRY_MAX_BACKOFF_SECONDS=100)
    def test_backoff_capped(self):
        self.assertEqual(compute_backoff_seconds(10), 100)


class RetryableJobTests(TestCase):
    def _delivery(self, max_retries=3):
        return CRMDelivery.objects.create(
            target=CRMTarget.INTERNAL,
            source_event_id="evt_1",
            max_retries=max_retries,
            status=DeliveryStatus.PENDING,
        )

    def test_failure_schedules_retry_then_permanent(self):
        d = self._delivery(max_retries=2)

        d.mark_failure("boom")
        self.assertEqual(d.retry_count, 1)
        self.assertEqual(d.status, DeliveryStatus.FAILED)
        self.assertIsNotNone(d.next_retry_at)

        d.mark_failure("boom again")
        self.assertEqual(d.retry_count, 2)
        self.assertEqual(d.status, DeliveryStatus.PERMANENTLY_FAILED)
        self.assertIsNone(d.next_retry_at)

    def test_success_clears_retry_state(self):
        d = self._delivery()
        d.mark_failure("temporary")
        d.mark_success()
        self.assertEqual(d.status, DeliveryStatus.SUCCESS)
        self.assertIsNone(d.next_retry_at)
        self.assertEqual(d.last_error, "")

    def test_manual_retry_makes_job_due(self):
        d = self._delivery()
        d.mark_failure("temporary")
        d.reset_for_manual_retry()
        self.assertEqual(d.status, DeliveryStatus.PENDING)
        self.assertTrue(d.is_due)
