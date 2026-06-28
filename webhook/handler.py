"""Twilio WhatsApp webhook handling for DropInvoice."""

from __future__ import annotations

import html
import logging
import mimetypes
import os
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, BackgroundTasks, Request, Response

from processing.intent import (
    MENU_MESSAGE,
    detect_options,
    parse_menu_reply,
    should_show_menu,
)
from webhook.pending import (
    PendingRequest,
    clear_pending,
    get_pending,
    set_pending,
)

logger = logging.getLogger("dropinvoice.webhook")
router = APIRouter()

ACK_MESSAGE = "Processing your invoice..."
UNSUPPORTED_MESSAGE = (
    "Send a bill photo, a voice note, or type your items "
    "(e.g. '5kg rice at 60, 2kg dal at 120') to create an invoice."
)
MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 30


class MediaKind(str, Enum):
    """Supported media categories for WhatsApp invoice creation."""

    IMAGE = "image"
    AUDIO = "audio"


@dataclass(frozen=True)
class IncomingMessage:
    """Normalized Twilio webhook payload fields used by DropInvoice."""

    from_number: str
    to_number: str
    body: str
    message_sid: str
    account_sid: str
    num_media: int
    media_url: str | None
    media_content_type: str | None


@dataclass(frozen=True)
class DownloadedMedia:
    """Downloaded media details passed into downstream processing."""

    path: str
    kind: MediaKind
    content_type: str


@router.post("/webhook")
async def receive_whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Receive a Twilio WhatsApp webhook and acknowledge it immediately."""

    form_data = await request.form()
    incoming = parse_incoming_message(dict(form_data))
    now = time.time()

    # 0. Is this a reply to a menu we asked earlier?
    pending = get_pending(incoming.from_number, now)
    if pending is not None:
        choices = parse_menu_reply(incoming.body)
        if choices is not None:
            clear_pending(incoming.from_number)
            logger.info("Resuming pending request for %s with menu choices", incoming.from_number)
            background_tasks.add_task(resume_pending_request, pending, choices)
            return twiml_response(ACK_MESSAGE)
        # Not a valid menu reply — drop the stale request and handle as new.
        clear_pending(incoming.from_number)

    # 1. Media message (photo/voice bill).
    if incoming.media_url and incoming.media_content_type:
        media_kind = classify_media(incoming.media_content_type)
        if media_kind is None:
            logger.info(
                "Ignoring unsupported media type %s from %s",
                incoming.media_content_type, incoming.from_number,
            )
            return twiml_response(UNSUPPORTED_MESSAGE)

        options = detect_options(incoming.body)
        if should_show_menu(incoming.body, options, media_kind.value):
            set_pending(
                PendingRequest(
                    from_number=incoming.from_number,
                    media_kind=media_kind.value,
                    incoming=incoming,
                ),
                now,
            )
            logger.info("Asking menu before processing media from %s", incoming.from_number)
            return twiml_response(MENU_MESSAGE)

        background_tasks.add_task(download_and_route_media, incoming, media_kind)
        return twiml_response(ACK_MESSAGE)

    # 2. Text message.
    if looks_like_invoice_text(incoming.body):
        options = detect_options(incoming.body)
        if should_show_menu(incoming.body, options, "text"):
            set_pending(
                PendingRequest(
                    from_number=incoming.from_number,
                    media_kind="text",
                    body=incoming.body,
                    metadata=build_text_metadata(incoming),
                ),
                now,
            )
            logger.info("Asking menu before processing text from %s", incoming.from_number)
            return twiml_response(MENU_MESSAGE)
        logger.info("Routing text message to invoice pipeline from %s", incoming.from_number)
        background_tasks.add_task(route_text_to_pipeline, incoming)
        return twiml_response(ACK_MESSAGE)

    logger.info("Ignoring non-invoice text webhook from %s", incoming.from_number)
    return twiml_response(UNSUPPORTED_MESSAGE)


def resume_pending_request(pending: PendingRequest, choices: Any) -> None:
    """Run a parked request once the customer has answered the menu.

    Text requests are re-run from the stored body; media requests are downloaded
    now (we deferred the download until the customer confirmed their choices).
    """

    try:
        if pending.media_kind == "text":
            metadata = dict(pending.metadata)
            media_path = ""
        else:
            incoming = pending.incoming
            media = download_twilio_media(incoming, MediaKind(pending.media_kind))
            metadata = build_processing_metadata(incoming, media)
            media_path = media.path

        metadata["doc_type"] = choices.doc_type
        metadata["gst_choice"] = choices.gst
        metadata["tally_choice"] = choices.tally
        run_pipeline_sync(media_path, metadata)
    except Exception:
        logger.exception("Failed to resume pending request for %s", pending.from_number)


def parse_incoming_message(payload: dict[str, Any]) -> IncomingMessage:
    """Normalize Twilio form fields into a typed incoming message object."""

    return IncomingMessage(
        from_number=str(payload.get("From", "")),
        to_number=str(payload.get("To", "")),
        body=str(payload.get("Body", "")),
        message_sid=str(payload.get("MessageSid", "")),
        account_sid=str(payload.get("AccountSid", "")),
        num_media=parse_int(payload.get("NumMedia"), default=0),
        media_url=optional_string(payload.get("MediaUrl0")),
        media_content_type=optional_string(payload.get("MediaContentType0")),
    )


def parse_int(value: Any, default: int) -> int:
    """Parse an integer form value while preserving a safe default."""

    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def optional_string(value: Any) -> str | None:
    """Return a non-empty stripped string, or None when the field is blank."""

    if value is None:
        return None

    stripped = str(value).strip()
    return stripped or None


def classify_media(content_type: str) -> MediaKind | None:
    """Classify a Twilio media MIME type into a supported processing kind."""

    normalized = content_type.lower().split(";", maxsplit=1)[0].strip()

    if normalized.startswith("image/"):
        return MediaKind.IMAGE

    if normalized.startswith("audio/"):
        return MediaKind.AUDIO

    return None


def download_and_route_media(incoming: IncomingMessage, media_kind: MediaKind) -> None:
    """Download Twilio media and hand it to the correct invoice pipeline."""

    try:
        media = download_twilio_media(incoming, media_kind)
        route_media_to_pipeline(media, incoming)
    except Exception:
        logger.exception("Failed to process Twilio message %s", incoming.message_sid)


def download_twilio_media(
    incoming: IncomingMessage,
    media_kind: MediaKind,
) -> DownloadedMedia:
    """Download the first Twilio media attachment to local temporary storage."""

    if not incoming.media_url or not incoming.media_content_type:
        raise ValueError("Twilio media URL and content type are required")

    account_sid = os.getenv("TWILIO_ACCOUNT_SID") or incoming.account_sid
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")

    if not account_sid or not auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are required")

    response = requests.get(
        incoming.media_url,
        auth=(account_sid, auth_token),
        stream=True,
        timeout=MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    destination = build_media_path(incoming, incoming.media_content_type)
    with destination.open("wb") as file_handle:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if chunk:
                file_handle.write(chunk)

    logger.info("Downloaded %s media to %s", media_kind.value, destination)
    return DownloadedMedia(
        path=str(destination),
        kind=media_kind,
        content_type=incoming.media_content_type,
    )


def build_media_path(incoming: IncomingMessage, content_type: str) -> Path:
    """Build a stable temporary path for downloaded Twilio media."""

    extension = mimetypes.guess_extension(content_type.split(";", maxsplit=1)[0]) or ".bin"
    safe_message_sid = incoming.message_sid or "unknown-message"
    upload_dir = Path(tempfile.gettempdir()) / "dropinvoice" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir / f"{safe_message_sid}{extension}"


def looks_like_invoice_text(body: str) -> bool:
    """Return True when a text message looks like an item/price list, not a greeting.

    We require at least one digit (a quantity or price) and a couple of words, so
    casual messages like "hi" don't trigger invoice generation.
    """

    text = (body or "").strip()
    if len(text) < 3:
        return False
    if not any(character.isdigit() for character in text):
        return False
    # Ignore Twilio sandbox control words just in case they reach us.
    if text.lower().startswith(("join ", "stop", "start", "help")):
        return False
    return True


def route_text_to_pipeline(incoming: IncomingMessage) -> None:
    """Run the invoice pipeline on a plain text message describing items."""

    metadata = build_text_metadata(incoming)
    run_pipeline_sync("", metadata)


def build_text_metadata(incoming: IncomingMessage) -> dict[str, Any]:
    """Build pipeline metadata for a text-only invoice request."""

    return {
        "from_number": incoming.from_number,
        "to_number": incoming.to_number,
        "message_sid": incoming.message_sid,
        "account_sid": incoming.account_sid,
        "body": incoming.body,
        "num_media": 0,
        "media_kind": "text",
        "media_content_type": "text/plain",
    }


def route_media_to_pipeline(media: DownloadedMedia, incoming: IncomingMessage) -> None:
    """Route downloaded media to Celery or the synchronous processing pipeline."""

    metadata = build_processing_metadata(incoming, media)

    if enqueue_celery_task(media, metadata):
        return

    # Synchronous fallback: run the full pipeline in-process
    run_pipeline_sync(media.path, metadata)


def run_pipeline_sync(media_path: str, metadata: dict[str, Any]) -> None:
    """Run the full invoice pipeline synchronously when Celery is unavailable."""

    try:
        from tasks.celery_tasks import run_invoice_pipeline
        run_invoice_pipeline(media_path, metadata)
    except Exception:
        logger.exception("Sync pipeline failed for %s", metadata.get("message_sid"))


def build_processing_metadata(
    incoming: IncomingMessage,
    media: DownloadedMedia,
) -> dict[str, Any]:
    """Build serializable metadata for async invoice processing."""

    return {
        "from_number": incoming.from_number,
        "to_number": incoming.to_number,
        "message_sid": incoming.message_sid,
        "account_sid": incoming.account_sid,
        "body": incoming.body,
        "num_media": incoming.num_media,
        "media_kind": media.kind.value,
        "media_content_type": media.content_type,
    }


def enqueue_celery_task(media: DownloadedMedia, metadata: dict[str, Any]) -> bool:
    """Queue the full invoice pipeline when Celery + Redis are available.

    Celery is opt-in: set USE_CELERY=true (and run a worker) to enable the
    async queue. By default the pipeline runs synchronously in-process, which
    is simpler and more robust for prototype/single-host deployments.
    """

    if os.getenv("USE_CELERY", "false").strip().lower() not in {"1", "true", "yes"}:
        logger.info("USE_CELERY not enabled — running pipeline synchronously")
        return False

    try:
        from tasks.celery_tasks import process_invoice_task
    except ModuleNotFoundError:
        return False

    try:
        process_invoice_task.delay(media.path, metadata)
        logger.info("Queued Celery invoice task for %s", metadata["message_sid"])
        return True
    except Exception:
        logger.info("Celery/Redis unavailable — falling back to sync pipeline")
        return False




def twiml_response(message: str) -> Response:
    """Build a Twilio MessagingResponse XML payload."""

    escaped_message = html.escape(message, quote=False)
    return Response(
        content=f"<Response><Message>{escaped_message}</Message></Response>",
        media_type="application/xml",
    )

