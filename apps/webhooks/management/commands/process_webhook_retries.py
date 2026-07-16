"""
Retry runner: processes webhook events and CRM deliveries whose exponential
backoff window has elapsed. Intended to run on a schedule (cron / task runner).

    python manage.py process_webhook_retries
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.crm.services import process_due_crm_deliveries
from apps.webhooks.services import process_due_webhook_events


class Command(BaseCommand):
    help = "Process due webhook + CRM delivery retries using exponential backoff."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Max jobs to process per queue (default: 100).",
        )

    def handle(self, *args, **options) -> None:
        limit = options["limit"]

        self.stdout.write("Processing due webhook events...")
        wh = process_due_webhook_events(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"  webhooks: processed={wh['processed']} "
                f"succeeded={wh['succeeded']} failed={wh['failed']}"
            )
        )

        self.stdout.write("Processing due CRM deliveries...")
        crm = process_due_crm_deliveries(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"  crm: processed={crm['processed']} "
                f"succeeded={crm['succeeded']} failed={crm['failed']}"
            )
        )
        self.stdout.write(self.style.SUCCESS("Retry run complete."))
