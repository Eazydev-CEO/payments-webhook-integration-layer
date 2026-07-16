"""Aggregated REST API routes mounted under /api/."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.api import views

router = DefaultRouter()
router.register("payment-intents", views.PaymentIntentViewSet, basename="payment-intent")
router.register("webhook-events", views.WebhookEventViewSet, basename="webhook-event")
router.register("crm-deliveries", views.CRMDeliveryViewSet, basename="crm-delivery")
router.register("settlements", views.SettlementViewSet, basename="settlement")

urlpatterns = [
    # Webhook receivers (open; signature-authenticated).
    path("webhooks/stripe/", views.StripeWebhookView.as_view(), name="webhook-stripe"),
    path("webhooks/paystack/", views.PaystackWebhookView.as_view(), name="webhook-paystack"),
    path("webhooks/internal/", views.InternalWebhookView.as_view(), name="webhook-internal"),
    # Routed resources.
    path("", include(router.urls)),
]
