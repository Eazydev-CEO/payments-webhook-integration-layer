"""
Unified REST API for the payments + webhook integration layer.

Management endpoints (payment intents, settlements, retries) require an
authenticated session. Webhook receivers are open (AllowAny) because their
authentication *is* the processor signature, verified in the service layer.
"""
from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import client_ip
from apps.crm.models import CRMDelivery
from apps.crm.services import retry_crm_delivery
from apps.payments.models import PaymentIntent
from apps.payments.services import (
    PaymentError,
    create_payment_intent,
    mark_demo_payment,
)
from apps.processors.models import ProcessorCode
from apps.settlements.models import SettlementBatch
from apps.settlements.services import SettlementError, import_and_reconcile
from apps.webhooks.models import WebhookEvent
from apps.webhooks.services import WebhookError, ingest_webhook, retry_webhook_event

from . import serializers


# ==========================================================================
# Payment intents
# ==========================================================================
class PaymentIntentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Create, list, retrieve payment intents and mark demo outcomes."""

    queryset = PaymentIntent.objects.select_related("processor").all()
    serializer_class = serializers.PaymentIntentSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "reference"
    lookup_value_regex = "[^/]+"
    filterset_fields = ["status", "currency", "processor__code"]
    search_fields = ["reference", "customer_name", "customer_email", "idempotency_key"]
    ordering_fields = ["created_at", "amount"]

    @extend_schema(
        request=serializers.CreatePaymentIntentSerializer,
        responses=serializers.PaymentIntentSerializer,
        examples=[
            OpenApiExample(
                "Create intent",
                value={
                    "idempotency_key": "order-1001",
                    "processor": "stripe",
                    "customer_name": "Ada Lovelace",
                    "customer_email": "ada@example.com",
                    "amount": "149.99",
                    "currency": "USD",
                    "metadata": {"order_id": "1001"},
                },
                request_only=True,
            )
        ],
    )
    def create(self, request):
        payload = serializers.CreatePaymentIntentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        try:
            result = create_payment_intent(
                idempotency_key=data["idempotency_key"],
                processor_code=data["processor"],
                customer_name=data["customer_name"],
                customer_email=data["customer_email"],
                amount=data["amount"],
                currency=data.get("currency", "USD"),
                metadata=data.get("metadata") or {},
                actor=request.user,
                ip_address=client_ip(request),
            )
        except PaymentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        body = serializers.PaymentIntentSerializer(result.intent).data
        body["idempotent_replay"] = not result.created
        return Response(
            body,
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )

    @extend_schema(
        request=serializers.MarkPaymentSerializer,
        responses=serializers.PaymentIntentSerializer,
    )
    @action(detail=True, methods=["post"])
    def mark(self, request, reference=None):
        """Mark a demo/manual payment intent as succeeded or failed."""
        intent = self.get_object()
        payload = serializers.MarkPaymentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            mark_demo_payment(
                intent=intent,
                outcome=payload.validated_data["outcome"],
                actor=request.user,
                ip_address=client_ip(request),
            )
        except PaymentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        intent.refresh_from_db()
        return Response(serializers.PaymentIntentSerializer(intent).data)


# ==========================================================================
# Webhook receivers (open; signature is the authentication)
# ==========================================================================
@method_decorator(csrf_exempt, name="dispatch")
class BaseWebhookView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    processor_code: str = ""

    @extend_schema(
        request={"application/json": {"type": "object"}},
        responses=OpenApiResponse(description="Ingestion result"),
    )
    def post(self, request):
        raw_body = request.body.decode("utf-8") if request.body else ""
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            result = ingest_webhook(
                processor_code=self.processor_code,
                raw_body=raw_body,
                headers=headers,
                actor=getattr(request, "user", None),
                ip_address=client_ip(request),
            )
        except WebhookError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result.as_response(), status=result.http_status)


class StripeWebhookView(BaseWebhookView):
    processor_code = ProcessorCode.STRIPE


class PaystackWebhookView(BaseWebhookView):
    processor_code = ProcessorCode.PAYSTACK


class InternalWebhookView(BaseWebhookView):
    """Internal normalized event endpoint (manual/demo processor)."""

    processor_code = ProcessorCode.MANUAL


# ==========================================================================
# Webhook + CRM management (read + retry)
# ==========================================================================
class WebhookEventViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = WebhookEvent.objects.select_related("processor").all()
    serializer_class = serializers.WebhookEventSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status", "processor__code", "signature_verified", "is_duplicate"]
    search_fields = ["event_id", "event_type"]

    @extend_schema(responses=serializers.WebhookEventSerializer)
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        event = self.get_object()
        ok = retry_webhook_event(event, actor=request.user)
        event.refresh_from_db()
        body = serializers.WebhookEventSerializer(event).data
        body["retry_succeeded"] = ok
        return Response(body)


class CRMDeliveryViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = CRMDelivery.objects.all()
    serializer_class = serializers.CRMDeliverySerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status", "target", "event_type"]
    search_fields = ["source_event_id", "event_type"]

    @extend_schema(responses=serializers.CRMDeliverySerializer)
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        delivery = self.get_object()
        ok = retry_crm_delivery(delivery, actor=request.user)
        delivery.refresh_from_db()
        body = serializers.CRMDeliverySerializer(delivery).data
        body["retry_succeeded"] = ok
        return Response(body)


# ==========================================================================
# Settlements
# ==========================================================================
class SettlementViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = SettlementBatch.objects.select_related("processor").prefetch_related("items")
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status", "processor__code", "currency"]
    search_fields = ["reference"]

    def get_serializer_class(self):
        if self.action == "list":
            return serializers.SettlementBatchListSerializer
        return serializers.SettlementBatchSerializer

    @extend_schema(
        request=serializers.ImportSettlementSerializer,
        responses=serializers.SettlementBatchSerializer,
        description="Import a settlement CSV and reconcile it against internal payments.",
    )
    @action(detail=False, methods=["post"], url_path="import")
    def import_csv(self, request):
        payload = serializers.ImportSettlementSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        upload = data.get("file")
        if upload is not None:
            csv_content = upload.read().decode("utf-8")
            filename = upload.name
        else:
            csv_content = data["csv_content"]
            filename = "inline.csv"

        try:
            batch = import_and_reconcile(
                processor_code=data["processor"],
                reference=data["reference"],
                statement_date=data["statement_date"],
                csv_content=csv_content,
                currency=data.get("currency", "USD"),
                source_filename=filename,
                actor=request.user,
            )
        except SettlementError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            serializers.SettlementBatchSerializer(batch).data,
            status=status.HTTP_201_CREATED,
        )
