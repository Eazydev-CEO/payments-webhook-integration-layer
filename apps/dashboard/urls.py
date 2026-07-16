from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    # Payment intents
    path("intents/", views.intents, name="intents"),
    path("intents/create/", views.intent_create, name="intent_create"),
    path("intents/<str:reference>/", views.intent_detail, name="intent_detail"),
    path("intents/<str:reference>/mark/", views.intent_mark, name="intent_mark"),
    # Transactions
    path("transactions/", views.transactions, name="transactions"),
    # Webhooks
    path("webhooks/", views.webhooks, name="webhooks"),
    path("webhooks/simulate/", views.webhook_simulate, name="webhook_simulate"),
    path("webhooks/failed/", views.failed_webhooks, name="failed_webhooks"),
    path("webhooks/<int:pk>/retry/", views.webhook_retry, name="webhook_retry"),
    # CRM
    path("crm/", views.crm, name="crm"),
    path("crm/<int:pk>/retry/", views.crm_retry, name="crm_retry"),
    # Settlements + reconciliation
    path("settlements/", views.settlements, name="settlements"),
    path("settlements/import/", views.settlement_import, name="settlement_import"),
    path("settlements/<int:pk>/", views.reconciliation, name="reconciliation"),
    # Audit
    path("audit/", views.audit, name="audit"),
    # Settings
    path("settings/", views.settings_view, name="settings"),
]
