"""Twilio WhatsApp webhook handling for DropInvoice."""

from __future__ import annotations

import html
import logging
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, BackgroundTasks, Request, Response

logger = logging.getLogger("dropinvoice.webhook")
router = APIRouter()

ACK_MESSAGE = "Processing your invoice..."
UNSUPPORTED_MESSAGE = "Please send a bill photo or voice note to create an invoice."
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

    if not incoming.media_url or not incoming.media_content_type:
        logger.info("Ignoring text-only webhook from %s", incoming.from_number)
        return twiml_response(UNSUPPORTED_MESSAGE)

    media_kind = classify_media(incoming.media_content_type)
    if media_kind is None:
        logger.info(
            "Ignoring unsupported media type %s from %s",
            incoming.media_content_type,
            incoming.from_number,
        )
        return twiml_response(UNSUPPORTED_MESSAGE)

    background_tasks.add_task(download_and_route_media, incoming, media_kind)
    return twiml_response(ACK_MESSAGE)


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


def route_media_to_pipeline(media: DownloadedMedia, incoming: IncomingMessage) -> None:
    """Route downloaded media to Celery or the synchronous processing pipeline."""

    metadata = build_processing_metadata(incoming, media)

    if enqueue_celery_task(media, metadata):
        return

    if media.kind == MediaKind.IMAGE:
        route_image_to_pipeline(media.path, metadata)
        return

    if media.kind == MediaKind.AUDIO:
        route_audio_to_pipeline(media.path, metadata)
        return

    raise ValueError(f"Unsupported media kind: {media.kind}")


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
    """Queue the full invoice pipeline when the Celery task module exists."""

    try:
        from tasks.celery_tasks import process_invoice_task
    except ModuleNotFoundError:
        return False

    process_invoice_task.delay(media.path, metadata)
    logger.info("Queued Celery invoice task for %s", metadata["message_sid"])
    return True


def route_image_to_pipeline(media_path: str, metadata: dict[str, Any]) -> None:
    """Route an image attachment to the OCR pipeline when available."""

    try:
        from processing.image_processor import process_image
    except ModuleNotFoundError:
        logger.warning("Image pipeline is not implemented yet for %s", media_path)
        return

    process_image(media_path, metadata)


def route_audio_to_pipeline(media_path: str, metadata: dict[str, Any]) -> None:
    """Route an audio attachment to the Whisper transcription pipeline when available."""

    try:
        from processing.audio_processor import process_audio
    except ModuleNotFoundError:
        logger.warning("Audio pipeline is not implemented yet for %s", media_path)
        return

    process_audio(media_path, metadata)


def twiml_response(message: str) -> Response:
    """Build a Twilio MessagingResponse XML payload."""

    escaped_message = html.escape(message, quote=False)
    return Response(
        content=f"<Response><Message>{escaped_message}</Message></Response>",
        media_type="application/xml",
    )

