"""
Operator dashboard views.

Read-only pages render metrics and tables; a handful of POST endpoints drive
operational actions (simulate webhook, retry delivery, mark demo payment,
import settlement) — each protected by login + CSRF and audit-logged in the
service layer.
"""
from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.models import AuditLog
from apps.common.retry import DeliveryStatus
from apps.crm.models import CRMDelivery
from apps.crm.services import retry_crm_delivery
from apps.payments.models import (
    PaymentIntent,
    PaymentStatus,
    PaymentTransaction,
)
from apps.payments.services import PaymentError, create_payment_intent, mark_demo_payment
from apps.processors.models import PaymentProcessor
from apps.settlements.models import MatchStatus, SettlementBatch
from apps.settlements.services import SettlementError, import_and_reconcile
from apps.webhooks.models import WebhookEvent
from apps.webhooks.services import ingest_webhook, retry_webhook_event
from apps.webhooks.simulate import build_signed_event

from . import metrics
from .metrics import resolve_window


def _paginate(request, queryset, per_page: int = 20):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))


# ==========================================================================
# Overview
# ==========================================================================
@login_required
def overview(request):
    window = resolve_window(request)
    data = metrics.overview_metrics(window)
    series = metrics.volume_series(window)

    recent_intents = PaymentIntent.objects.select_related("processor")[:6]
    recent_webhooks = WebhookEvent.objects.select_related("processor")[:6]

    context = {
        "active": "overview",
        "window": window,
        "metrics": data,
        "chart_labels": json.dumps(series["labels"]),
        "chart_values": json.dumps(series["values"]),
        "recent_intents": recent_intents,
        "recent_webhooks": recent_webhooks,
        "range_options": [
            ("today", "Today"), ("7d", "7D"), ("1m", "1M"),
            ("3m", "3M"), ("all", "All"),
        ],
    }
    return render(request, "dashboard/overview.html", context)


# ==========================================================================
# Payment intents
# ==========================================================================
@login_required
def intents(request):
    qs = PaymentIntent.objects.select_related("processor").all()
    status = request.GET.get("status")
    processor = request.GET.get("processor")
    q = request.GET.get("q")
    if status:
        qs = qs.filter(status=status)
    if processor:
        qs = qs.filter(processor__code=processor)
    if q:
        qs = qs.filter(reference__icontains=q) | qs.filter(customer_email__icontains=q)

    context = {
        "active": "intents",
        "page_obj": _paginate(request, qs),
        "processors": PaymentProcessor.objects.all(),
        "status_choices": PaymentStatus.choices,
        "filters": {"status": status or "", "processor": processor or "", "q": q or ""},
    }
    return render(request, "dashboard/intents.html", context)


@login_required
def intent_detail(request, reference):
    intent = get_object_or_404(
        PaymentIntent.objects.select_related("processor"), reference=reference
    )
    context = {
        "active": "intents",
        "intent": intent,
        "transactions": intent.transactions.all(),
        "crm_deliveries": intent.crm_deliveries.all(),
        "settlement_items": intent.settlement_items.select_related("batch"),
    }
    return render(request, "dashboard/intent_detail.html", context)


@login_required
@require_POST
def intent_create(request):
    try:
        result = create_payment_intent(
            idempotency_key=request.POST.get("idempotency_key", "").strip(),
            processor_code=request.POST.get("processor", ""),
            customer_name=request.POST.get("customer_name", "").strip(),
            customer_email=request.POST.get("customer_email", "").strip(),
            amount=request.POST.get("amount", "0"),
            currency=request.POST.get("currency", "USD"),
            metadata={"source": "dashboard"},
            actor=request.user,
        )
    except PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("dashboard:intents")

    if result.created:
        messages.success(request, f"Created payment intent {result.intent.reference}.")
    else:
        messages.info(
            request,
            f"Idempotent replay — key already used by {result.intent.reference}.",
        )
    return redirect("dashboard:intent_detail", reference=result.intent.reference)


@login_required
@require_POST
def intent_mark(request, reference):
    intent = get_object_or_404(PaymentIntent, reference=reference)
    try:
        mark_demo_payment(
            intent=intent,
            outcome=request.POST.get("outcome", ""),
            actor=request.user,
        )
        messages.success(request, f"Marked {intent.reference} as {request.POST.get('outcome')}.")
    except PaymentError as exc:
        messages.error(request, str(exc))
    return redirect("dashboard:intent_detail", reference=reference)


# ==========================================================================
# Transactions
# ==========================================================================
@login_required
def transactions(request):
    qs = PaymentTransaction.objects.select_related("intent", "processor").all()
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    context = {
        "active": "transactions",
        "page_obj": _paginate(request, qs),
        "filters": {"status": status or ""},
    }
    return render(request, "dashboard/transactions.html", context)


# ==========================================================================
# Webhooks
# ==========================================================================
@login_required
def webhooks(request):
    qs = WebhookEvent.objects.select_related("processor").all()
    processor = request.GET.get("processor")
    status = request.GET.get("status")
    q = request.GET.get("q")
    if processor:
        qs = qs.filter(processor__code=processor)
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(event_id__icontains=q)

    context = {
        "active": "webhooks",
        "page_obj": _paginate(request, qs),
        "processors": PaymentProcessor.objects.all(),
        "intents": PaymentIntent.objects.select_related("processor")[:50],
        "status_choices": DeliveryStatus.choices,
        "filters": {"processor": processor or "", "status": status or "", "q": q or ""},
    }
    return render(request, "dashboard/webhooks.html", context)


@login_required
def failed_webhooks(request):
    qs = WebhookEvent.objects.select_related("processor").filter(
        status__in=[DeliveryStatus.FAILED, DeliveryStatus.PERMANENTLY_FAILED]
    )
    context = {
        "active": "failed_webhooks",
        "page_obj": _paginate(request, qs),
    }
    return render(request, "dashboard/failed_webhooks.html", context)


@login_required
@require_POST
def webhook_retry(request, pk):
    event = get_object_or_404(WebhookEvent, pk=pk)
    ok = retry_webhook_event(event, actor=request.user)
    if ok:
        messages.success(request, f"Webhook {event.event_id} reprocessed successfully.")
    else:
        messages.warning(request, f"Webhook {event.event_id} retry failed — will back off.")
    return redirect(request.META.get("HTTP_REFERER", "dashboard:webhooks"))


@login_required
@require_POST
def webhook_simulate(request):
    reference = request.POST.get("reference", "").strip()
    outcome = request.POST.get("outcome", "success")
    succeeded = outcome == "success"

    intent = PaymentIntent.objects.filter(reference=reference).select_related("processor").first()
    if intent is None:
        messages.error(request, "Select a valid payment intent to simulate against.")
        return redirect("dashboard:webhooks")

    raw_body, headers = build_signed_event(
        intent.processor.code,
        reference=intent.reference,
        amount=intent.amount,
        currency=intent.currency,
        email=intent.customer_email,
        succeeded=succeeded,
    )
    result = ingest_webhook(
        processor_code=intent.processor.code,
        raw_body=raw_body,
        headers=headers,
        actor=request.user,
    )
    if result.duplicate:
        messages.info(request, "Simulated a duplicate webhook — stored and ignored.")
    elif result.accepted:
        messages.success(
            request,
            f"Simulated {intent.processor.code} webhook for {intent.reference}.",
        )
    else:
        messages.warning(request, f"Webhook rejected: {result.note}.")
    return redirect("dashboard:webhooks")


# ==========================================================================
# CRM deliveries
# ==========================================================================
@login_required
def crm(request):
    qs = CRMDelivery.objects.select_related("payment_intent").all()
    target = request.GET.get("target")
    status = request.GET.get("status")
    if target:
        qs = qs.filter(target=target)
    if status:
        qs = qs.filter(status=status)
    context = {
        "active": "crm",
        "page_obj": _paginate(request, qs),
        "status_choices": DeliveryStatus.choices,
        "filters": {"target": target or "", "status": status or ""},
    }
    return render(request, "dashboard/crm.html", context)


@login_required
@require_POST
def crm_retry(request, pk):
    delivery = get_object_or_404(CRMDelivery, pk=pk)
    ok = retry_crm_delivery(delivery, actor=request.user)
    if ok:
        messages.success(request, f"CRM delivery #{delivery.pk} succeeded.")
    else:
        messages.warning(request, f"CRM delivery #{delivery.pk} failed — will back off.")
    return redirect(request.META.get("HTTP_REFERER", "dashboard:crm"))


# ==========================================================================
# Settlements + reconciliation
# ==========================================================================
@login_required
def settlements(request):
    qs = SettlementBatch.objects.select_related("processor").all()
    context = {
        "active": "settlements",
        "page_obj": _paginate(request, qs),
        "processors": PaymentProcessor.objects.all(),
        "today": timezone.now().date().isoformat(),
    }
    return render(request, "dashboard/settlements.html", context)


@login_required
@require_POST
def settlement_import(request):
    upload = request.FILES.get("file")
    if upload is None:
        messages.error(request, "Please choose a CSV file to import.")
        return redirect("dashboard:settlements")
    try:
        content = upload.read().decode("utf-8")
        batch = import_and_reconcile(
            processor_code=request.POST.get("processor", ""),
            reference=request.POST.get("reference", "").strip(),
            statement_date=request.POST.get("statement_date") or timezone.now().date(),
            csv_content=content,
            currency=request.POST.get("currency", "USD"),
            source_filename=upload.name,
            actor=request.user,
        )
    except (SettlementError, ValueError) as exc:
        messages.error(request, f"Import failed: {exc}")
        return redirect("dashboard:settlements")
    messages.success(
        request,
        f"Imported {batch.reference}: difference {batch.difference} {batch.currency}.",
    )
    return redirect("dashboard:reconciliation", pk=batch.pk)


@login_required
def reconciliation(request, pk):
    batch = get_object_or_404(
        SettlementBatch.objects.select_related("processor"), pk=pk
    )
    items = batch.items.select_related("matched_intent").all()
    match_filter = request.GET.get("match")
    if match_filter:
        items = items.filter(match_status=match_filter)
    context = {
        "active": "settlements",
        "batch": batch,
        "items": items,
        "match_choices": MatchStatus.choices,
        "match_filter": match_filter or "",
    }
    return render(request, "dashboard/reconciliation.html", context)


# ==========================================================================
# Audit logs
# ==========================================================================
@login_required
def audit(request):
    qs = AuditLog.objects.select_related("actor").all()
    action = request.GET.get("action")
    if action:
        qs = qs.filter(action=action)
    context = {
        "active": "audit",
        "page_obj": _paginate(request, qs, per_page=30),
        "action_choices": AuditLog.Action.choices,
        "filters": {"action": action or ""},
    }
    return render(request, "dashboard/audit.html", context)


# ==========================================================================
# Settings / processors
# ==========================================================================
@login_required
def settings_view(request):
    context = {
        "active": "settings",
        "processors": PaymentProcessor.objects.all(),
    }
    return render(request, "dashboard/settings.html", context)
