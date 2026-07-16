from __future__ import annotations

from rest_framework import serializers

from apps.crm.models import CRMDelivery
from apps.payments.models import PaymentIntent, PaymentTransaction
from apps.processors.models import PaymentProcessor, ProcessorCode
from apps.settlements.models import SettlementBatch, SettlementItem
from apps.webhooks.models import WebhookEvent


class ProcessorSerializer(serializers.ModelSerializer):
    mode = serializers.CharField(source="mode_label", read_only=True)

    class Meta:
        model = PaymentProcessor
        fields = ["code", "name", "is_active", "supports_webhooks", "mode"]


class PaymentIntentSerializer(serializers.ModelSerializer):
    processor = serializers.CharField(source="processor.code", read_only=True)
    processor_name = serializers.CharField(source="processor.name", read_only=True)

    class Meta:
        model = PaymentIntent
        fields = [
            "reference", "idempotency_key", "processor", "processor_name",
            "customer_name", "customer_email", "amount", "currency",
            "status", "metadata", "created_at", "updated_at",
        ]
        read_only_fields = ["reference", "status", "created_at", "updated_at"]


class CreatePaymentIntentSerializer(serializers.Serializer):
    idempotency_key = serializers.CharField(max_length=80)
    processor = serializers.ChoiceField(choices=[c.value for c in ProcessorCode])
    customer_name = serializers.CharField(max_length=120)
    customer_email = serializers.EmailField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    currency = serializers.CharField(max_length=3, default="USD")
    metadata = serializers.DictField(required=False, default=dict)


class MarkPaymentSerializer(serializers.Serializer):
    outcome = serializers.ChoiceField(choices=["success", "failed"])


class TransactionSerializer(serializers.ModelSerializer):
    intent = serializers.CharField(source="intent.reference", read_only=True)
    processor = serializers.CharField(source="processor.code", read_only=True)

    class Meta:
        model = PaymentTransaction
        fields = [
            "id", "intent", "processor", "reference", "external_id",
            "amount", "currency", "status", "created_at",
        ]


class WebhookEventSerializer(serializers.ModelSerializer):
    processor = serializers.CharField(source="processor.code", read_only=True)

    class Meta:
        model = WebhookEvent
        fields = [
            "id", "processor", "event_id", "event_type", "signature_verified",
            "is_duplicate", "status", "retry_count", "max_retries",
            "next_retry_at", "last_error", "normalized", "received_at",
            "processed_at",
        ]


class CRMDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = CRMDelivery
        fields = [
            "id", "target", "event_type", "source_event_id", "status",
            "retry_count", "max_retries", "next_retry_at", "last_error",
            "response", "created_at",
        ]


class SettlementItemSerializer(serializers.ModelSerializer):
    matched_intent = serializers.CharField(
        source="matched_intent.reference", read_only=True, default=None
    )

    class Meta:
        model = SettlementItem
        fields = [
            "id", "external_reference", "amount", "currency",
            "reported_status", "match_status", "matched_intent", "detail",
        ]


class SettlementBatchSerializer(serializers.ModelSerializer):
    processor = serializers.CharField(source="processor.code", read_only=True)
    items = SettlementItemSerializer(many=True, read_only=True)

    class Meta:
        model = SettlementBatch
        fields = [
            "id", "reference", "processor", "statement_date", "currency",
            "expected_amount", "received_amount", "difference", "status",
            "summary", "created_at", "reconciled_at", "items",
        ]


class SettlementBatchListSerializer(serializers.ModelSerializer):
    processor = serializers.CharField(source="processor.code", read_only=True)
    mismatch_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = SettlementBatch
        fields = [
            "id", "reference", "processor", "statement_date", "currency",
            "expected_amount", "received_amount", "difference", "status",
            "mismatch_count", "created_at",
        ]


class ImportSettlementSerializer(serializers.Serializer):
    processor = serializers.CharField(max_length=20)
    reference = serializers.CharField(max_length=60)
    statement_date = serializers.DateField()
    currency = serializers.CharField(max_length=3, default="USD")
    file = serializers.FileField(required=False)
    csv_content = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("file") and not attrs.get("csv_content"):
            raise serializers.ValidationError("Provide either 'file' or 'csv_content'.")
        return attrs
