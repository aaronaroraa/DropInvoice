"""Celery task definitions for asynchronous DropInvoice pipeline processing.

Assumptions:
- Redis is running at the URL specified by REDIS_URL (default: redis://localhost:6379/0).
- Each task receives a media file path and metadata dict from the webhook handler.
- The full pipeline is: process → parse → calculate → generate PDF → deliver.
- Success and failure outcomes are logged to Supabase via the database layer.
- Celery workers are started with:
    celery -A tasks.celery_tasks worker --loglevel=info
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from celery import Celery

logger = logging.getLogger("dropinvoice.tasks")

# ---------------------------------------------------------------------------
# Celery app configuration
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "dropinvoice",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",               # JSON for safe cross-process transport
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",              # IST for Indian business context
    enable_utc=True,
    task_track_started=True,              # lets monitoring see "STARTED" state
    task_acks_late=True,                  # re-deliver if worker crashes mid-task
    worker_prefetch_multiplier=1,         # one task at a time (OCR/Whisper are CPU-heavy)
    task_soft_time_limit=120,             # warn after 2 min
    task_time_limit=180,                  # hard-kill after 3 min
    result_expires=3600,                  # discard results after 1 hour
)


# ---------------------------------------------------------------------------
# Main pipeline task
# ---------------------------------------------------------------------------

def run_invoice_pipeline(media_path: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Run the full invoice pipeline: extract → parse → enrich → PDF → upload → deliver.

    This is the shared core called by both the Celery task and the synchronous
    fallback path in the webhook handler.
    """

    from_number = metadata.get("from_number", "")
    media_kind = metadata.get("media_kind", "unknown")
    message_sid = metadata.get("message_sid", "unknown")

    logger.info(
        "Starting invoice pipeline for %s [%s] (msg %s)",
        from_number, media_kind, message_sid,
    )

    try:
        # Steps 1+2: Turn the media into structured invoice JSON.
        invoice_data = _extract_and_parse_invoice(media_path, media_kind, metadata)

        # Step 3: Enrich with user profile from Supabase
        invoice_data = _enrich_with_user_profile(invoice_data, from_number)

        # Step 4: Generate GST-compliant PDF
        pdf_path = _generate_pdf(invoice_data)
        invoice_data["pdf_path"] = pdf_path
        invoice_data["raw_input_type"] = media_kind
        invoice_data["phone_number"] = from_number

        # Step 5: Upload PDF to Supabase Storage and attach public URL
        invoice_data["pdf_url"] = _upload_pdf(pdf_path, invoice_data.get("invoice_number", ""))

        # Step 6: Save invoice record to Supabase
        _save_invoice_record(invoice_data)

        # Step 7: Deliver PDF via WhatsApp and email
        _deliver_invoice(from_number, pdf_path, invoice_data)

        logger.info(
            "Pipeline complete for %s → %s",
            message_sid, invoice_data.get("invoice_number"),
        )
        return {
            "status": "success",
            "invoice_number": invoice_data.get("invoice_number"),
            "pdf_path": pdf_path,
            "from_number": from_number,
        }

    except Exception as exc:
        logger.exception(
            "Pipeline failed for %s [%s]: %s", from_number, media_kind, exc
        )
        _log_failure(from_number, str(exc), media_kind)
        _send_error_reply(from_number, media_kind, exc)
        return {
            "status": "failed",
            "from_number": from_number,
            "error": str(exc),
        }


@celery_app.task(
    bind=True,
    name="dropinvoice.process_invoice",
    max_retries=2,
    default_retry_delay=10,
)
def process_invoice_task(
    self: Any,
    media_path: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Celery wrapper around run_invoice_pipeline with retry support."""

    try:
        return run_invoice_pipeline(media_path, metadata)
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {"status": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Pipeline steps (each wraps an existing module)
# ---------------------------------------------------------------------------

def _extract_and_parse_invoice(
    media_path: str,
    media_kind: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Produce structured invoice JSON from media.

    Primary path for images: a single Gemini Vision call reads the photo
    (printed, handwritten, regional-language, or rough) directly into the
    schema. Falls back to the OCR/transcript text path (Gemini-on-text, then
    regex) when Vision is unavailable or fails.
    """

    if media_kind == "image":
        try:
            from processing.parser import parse_invoice_from_image
            return parse_invoice_from_image(media_path, metadata)
        except Exception:
            logger.warning(
                "Gemini Vision parse failed; falling back to OCR text path",
                exc_info=True,
            )

    raw_text = _extract_raw_text(media_path, media_kind, metadata)
    return _parse_invoice(raw_text, metadata)


def _extract_raw_text(
    media_path: str,
    media_kind: str,
    metadata: dict[str, Any],
) -> str:
    """Route to image or audio processing based on media kind."""

    if media_kind == "image":
        from processing.image_processor import (
            process_image,
            transcribe_image_with_gemini,
        )

        # Prefer Gemini Vision (handles phone photos of receipts far better than
        # local Tesseract OCR); fall back to Tesseract if Gemini is unavailable.
        vision_text = transcribe_image_with_gemini(media_path)
        if vision_text:
            return vision_text
        return process_image(media_path, metadata)

    if media_kind == "audio":
        from processing.audio_processor import process_audio
        return process_audio(media_path, metadata)

    raise ValueError(f"Unsupported media kind: {media_kind}")


def _parse_invoice(
    raw_text: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Parse raw OCR/transcript text into the DropInvoice JSON schema."""

    from processing.parser import parse_invoice_text
    return parse_invoice_text(raw_text, metadata)


def _enrich_with_user_profile(
    invoice_data: dict[str, Any],
    from_number: str,
) -> dict[str, Any]:
    """Overlay Supabase user profile data onto the parsed invoice.

    If the user has a saved GSTIN or business name and the invoice is
    missing those fields, we fill them in automatically.
    """

    try:
        from database.supabase_client import get_user_profile, create_or_update_user

        profile = get_user_profile(from_number)

        if profile is None:
            # Auto-register the user with whatever we know from the invoice
            create_or_update_user(
                phone_number=from_number,
                name=invoice_data.get("seller_name"),
                gstin=invoice_data.get("seller_gstin"),
            )
            return invoice_data

        # Backfill missing invoice fields from the saved profile
        if not invoice_data.get("seller_name") or invoice_data["seller_name"] == "Unknown Seller":
            invoice_data["seller_name"] = profile.get("business_name") or invoice_data.get("seller_name")

        if not invoice_data.get("seller_gstin") and profile.get("gstin"):
            invoice_data["seller_gstin"] = profile["gstin"]

        return invoice_data

    except Exception:
        # Profile enrichment is best-effort; never fail the pipeline for it
        logger.warning("Could not enrich invoice with user profile", exc_info=True)
        return invoice_data


def _generate_pdf(invoice_data: dict[str, Any]) -> str:
    """Generate an invoice PDF and return the file path."""

    from invoice.generator import generate_invoice_pdf
    return generate_invoice_pdf(invoice_data)


def _upload_pdf(pdf_path: str, invoice_number: str) -> str | None:
    """Upload the PDF to Supabase Storage and return the public URL (best-effort)."""

    try:
        from database.supabase_client import upload_invoice_pdf
        return upload_invoice_pdf(pdf_path, invoice_number)
    except Exception:
        logger.warning("PDF upload to Supabase Storage failed; delivery will send text summary", exc_info=True)
        return None


def _save_invoice_record(invoice_data: dict[str, Any]) -> None:
    """Persist the invoice record to Supabase (best-effort)."""

    try:
        from database.supabase_client import save_invoice_record
        save_invoice_record(invoice_data)
    except Exception:
        logger.warning("Could not save invoice record to Supabase", exc_info=True)


def _deliver_invoice(
    from_number: str,
    pdf_path: str,
    invoice_data: dict[str, Any],
) -> None:
    """Send the invoice PDF via WhatsApp and email (if email is known)."""

    # WhatsApp delivery — send summary text even if PDF URL isn't available
    try:
        from delivery.whatsapp import send_invoice_pdf, send_summary_message

        pdf_url = invoice_data.get("pdf_url")
        if pdf_url:
            send_invoice_pdf(from_number, pdf_path, invoice_data, pdf_url)
        else:
            # No public URL available; send text summary only
            send_summary_message(from_number, invoice_data)
    except Exception:
        logger.warning("WhatsApp delivery failed for %s", from_number, exc_info=True)

    # Email delivery — only if user has a registered email
    try:
        from database.supabase_client import get_user_profile
        profile = get_user_profile(from_number)
        recipient_email = (profile or {}).get("email")

        if recipient_email:
            from delivery.email_sender import send_invoice_email
            send_invoice_email(recipient_email, pdf_path, invoice_data)
    except Exception:
        logger.warning("Email delivery failed for %s", from_number, exc_info=True)


# ---------------------------------------------------------------------------
# Error handling helpers
# ---------------------------------------------------------------------------

def _log_failure(
    from_number: str,
    error_message: str,
    media_kind: str,
) -> None:
    """Log a pipeline failure to Supabase (best-effort)."""

    try:
        from database.supabase_client import log_pipeline_failure
        log_pipeline_failure(from_number, error_message, media_kind)
    except Exception:
        logger.warning("Could not log failure to Supabase", exc_info=True)


def _send_error_reply(
    from_number: str,
    media_kind: str,
    exc: Exception,
) -> None:
    """Send a user-friendly WhatsApp error message based on failure type."""

    from processing.image_processor import UnreadableImageError
    from processing.audio_processor import ShortAudioError

    if isinstance(exc, UnreadableImageError):
        message = (
            "📸 Couldn't read your bill clearly. "
            "Please retake in better lighting and send again."
        )
    elif isinstance(exc, ShortAudioError):
        message = (
            "🎤 Voice note too short. "
            "Please describe your items clearly."
        )
    else:
        message = (
            "⚠️ Something went wrong while processing your invoice. "
            "Please try again or send a clearer image/voice note."
        )

    try:
        from delivery.whatsapp import send_summary_message
        # Reuse summary sender with a minimal data dict for error replies
        from twilio.rest import Client
        account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        whatsapp_from = os.getenv("TWILIO_WHATSAPP_FROM", "")

        if account_sid and auth_token and whatsapp_from:
            client = Client(account_sid, auth_token)
            to_number = from_number
            if not to_number.startswith("whatsapp:"):
                to_number = f"whatsapp:{to_number}"
            client.messages.create(
                from_=whatsapp_from,
                to=to_number,
                body=message,
            )
    except Exception:
        logger.warning("Could not send error reply to %s", from_number, exc_info=True)
