from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from apps.audit.services import client_ip, record_audit


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    record_audit(
        "login",
        f"{user.get_username()} logged in",
        actor=user,
        entity_type="User",
        entity_id=user.pk,
        ip_address=client_ip(request),
    )


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is None:
        return
    record_audit(
        "logout",
        f"{user.get_username()} logged out",
        actor=user,
        entity_type="User",
        entity_id=user.pk,
        ip_address=client_ip(request),
    )
