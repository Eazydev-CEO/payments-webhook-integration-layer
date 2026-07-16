from django.apps import AppConfig


class ProcessorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.processors"
    verbose_name = "Payment Processors"
