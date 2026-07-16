"""
Seed the default admin/operator user from environment variables:
ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_PASSWORD. Idempotent.

    python manage.py create_admin
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create (or update) the default admin user from env vars."

    def handle(self, *args, **options) -> None:
        User = get_user_model()
        username = settings.ADMIN_USERNAME
        email = settings.ADMIN_EMAIL
        password = settings.ADMIN_PASSWORD

        if not username or not password:
            self.stderr.write(
                self.style.WARNING("ADMIN_USERNAME/ADMIN_PASSWORD not set; skipping.")
            )
            return

        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": email, "is_staff": True, "is_superuser": True},
        )
        # Keep the seeded account aligned with env config.
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()

        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} admin user '{username}'."))
