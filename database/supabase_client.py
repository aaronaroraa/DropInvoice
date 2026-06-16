"""Supabase client for DropInvoice user profiles and invoice records.

Assumptions:
- Supabase project has two tables: `users` and `invoices` (schemas below).
- SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in the environment.
- Service-role key is used for server-side operations (no RLS bypass needed).
- Phone numbers are stored in E.164 format (+91XXXXXXXXXX).

-- =============================================================
-- SQL SCHEMA — run once in the Supabase SQL editor
-- =============================================================
--
-- CREATE TABLE IF NOT EXISTS users (
--     id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
--     phone_number  TEXT UNIQUE NOT NULL,
--     business_name TEXT,
--     gstin         TEXT,
--     email         TEXT,
--     state_code    TEXT,
--     created_at    TIMESTAMPTZ DEFAULT now(),
--     updated_at    TIMESTAMPTZ DEFAULT now()
-- );
--
-- CREATE INDEX idx_users_phone ON users (phone_number);
--
-- CREATE TABLE IF NOT EXISTS invoices (
--     id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
--     phone_number    TEXT NOT NULL REFERENCES users(phone_number),
--     invoice_number  TEXT UNIQUE NOT NULL,
--     invoice_date    DATE NOT NULL DEFAULT CURRENT_DATE,
--     seller_name     TEXT,
--     seller_gstin    TEXT,
--     buyer_name      TEXT,
--     buyer_gstin     TEXT,
--     subtotal        NUMERIC(12,2) NOT NULL DEFAULT 0,
--     tax_type        TEXT,
--     cgst            NUMERIC(12,2),
--     sgst            NUMERIC(12,2),
--     igst            NUMERIC(12,2),
--     grand_total     NUMERIC(12,2) NOT NULL DEFAULT 0,
--     pdf_path        TEXT,
--     raw_input_type  TEXT,            -- 'image' or 'audio'
--     status          TEXT DEFAULT 'completed',
--     error_message   TEXT,
--     created_at      TIMESTAMPTZ DEFAULT now()
-- );
--
-- CREATE INDEX idx_invoices_phone ON invoices (phone_number);
-- CREATE INDEX idx_invoices_date  ON invoices (invoice_date);
-- =============================================================
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger("dropinvoice.database")


class SupabaseClientError(Exception):
    """Raised when a Supabase operation fails."""


# ---------------------------------------------------------------------------
# Client bootstrap
# ---------------------------------------------------------------------------

def get_supabase_client() -> Any:
    """Build and return a Supabase client from environment credentials.

    Returns:
        A ``supabase.Client`` instance configured with the project URL
        and the service-role key.
    """

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        raise SupabaseClientError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required."
        )

    try:
        from supabase import create_client  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SupabaseClientError("supabase package is not installed.") from exc

    return create_client(url, key)


# ---------------------------------------------------------------------------
# User profile operations
# ---------------------------------------------------------------------------

def get_user_profile(phone_number: str) -> dict[str, Any] | None:
    """Fetch a user profile by phone number.

    Args:
        phone_number: E.164-formatted phone number (e.g. ``+919876543210``).

    Returns:
        A dict with user fields or ``None`` if no profile exists.
    """

    client = get_supabase_client()
    normalized_phone = _normalize_phone(phone_number)

    try:
        response = (
            client.table("users")
            .select("*")
            .eq("phone_number", normalized_phone)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise SupabaseClientError(
            f"Failed to fetch user profile for {normalized_phone}"
        ) from exc

    rows = response.data or []
    if not rows:
        logger.info("No user profile found for %s", normalized_phone)
        return None

    logger.info("Fetched user profile for %s", normalized_phone)
    return dict(rows[0])


def create_or_update_user(
    phone_number: str,
    name: str | None = None,
    gstin: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    """Create a new user profile or update an existing one.

    Uses Supabase upsert on the ``phone_number`` unique constraint so that
    the operation is idempotent.

    Args:
        phone_number: E.164-formatted phone number.
        name:         Business or personal name.
        gstin:        15-character Indian GSTIN (optional).
        email:        Contact email for invoice delivery.

    Returns:
        The upserted user row as a dict.
    """

    client = get_supabase_client()
    normalized_phone = _normalize_phone(phone_number)

    # Build the payload with only non-None values to avoid blanking fields
    payload: dict[str, Any] = {"phone_number": normalized_phone}
    if name is not None:
        payload["business_name"] = name
    if gstin is not None:
        payload["gstin"] = gstin.strip().upper()
        payload["state_code"] = gstin[:2] if len(gstin) >= 2 else None
    if email is not None:
        payload["email"] = email.strip().lower()

    try:
        response = (
            client.table("users")
            .upsert(payload, on_conflict="phone_number")
            .execute()
        )
    except Exception as exc:
        raise SupabaseClientError(
            f"Failed to upsert user for {normalized_phone}"
        ) from exc

    rows = response.data or []
    if not rows:
        raise SupabaseClientError("Upsert returned no data.")

    logger.info("Upserted user profile for %s", normalized_phone)
    return dict(rows[0])


# ---------------------------------------------------------------------------
# Invoice record operations
# ---------------------------------------------------------------------------

def save_invoice_record(invoice_data: dict[str, Any]) -> dict[str, Any]:
    """Persist a completed invoice record to Supabase.

    Args:
        invoice_data: Parsed and enriched invoice dict containing at minimum
                      ``invoice_number``, ``grand_total``, and a source
                      ``phone_number`` in the associated metadata.

    Returns:
        The inserted row as a dict including the generated ``id``.
    """

    client = get_supabase_client()
    tax_breakdown = invoice_data.get("tax_breakdown") or {}

    row = {
        "phone_number": _normalize_phone(
            invoice_data.get("phone_number")
            or invoice_data.get("from_number", "")
        ),
        "invoice_number": invoice_data.get("invoice_number", ""),
        "invoice_date": str(
            invoice_data.get("date") or date.today().isoformat()
        ),
        "seller_name": invoice_data.get("seller_name"),
        "seller_gstin": invoice_data.get("seller_gstin"),
        "buyer_name": invoice_data.get("buyer_name"),
        "buyer_gstin": invoice_data.get("buyer_gstin"),
        "subtotal": float(invoice_data.get("subtotal") or 0),
        "tax_type": tax_breakdown.get("type"),
        "cgst": _safe_float(tax_breakdown.get("cgst")),
        "sgst": _safe_float(tax_breakdown.get("sgst")),
        "igst": _safe_float(tax_breakdown.get("igst")),
        "grand_total": float(invoice_data.get("grand_total") or 0),
        "pdf_path": invoice_data.get("pdf_path"),
        "raw_input_type": invoice_data.get("raw_input_type"),
        "status": invoice_data.get("status", "completed"),
        "error_message": invoice_data.get("error_message"),
    }

    try:
        response = client.table("invoices").insert(row).execute()
    except Exception as exc:
        raise SupabaseClientError(
            f"Failed to save invoice {row['invoice_number']}"
        ) from exc

    rows = response.data or []
    if not rows:
        raise SupabaseClientError("Insert returned no data.")

    logger.info("Saved invoice record %s", row["invoice_number"])
    return dict(rows[0])


def log_pipeline_failure(
    phone_number: str,
    error_message: str,
    raw_input_type: str | None = None,
) -> dict[str, Any] | None:
    """Log a failed invoice processing attempt to Supabase.

    Creates a partial invoice record with ``status='failed'`` so that
    the operations team can triage OCR/ASR/Claude failures.

    Args:
        phone_number:   The user's phone number.
        error_message:  A human-readable error description.
        raw_input_type: ``'image'`` or ``'audio'``.

    Returns:
        The inserted failure row, or ``None`` if logging itself fails.
    """

    try:
        return save_invoice_record({
            "phone_number": phone_number,
            "invoice_number": f"FAILED-{date.today().isoformat()}",
            "grand_total": 0,
            "subtotal": 0,
            "status": "failed",
            "error_message": error_message[:500],  # cap length for DB
            "raw_input_type": raw_input_type,
        })
    except Exception:
        logger.exception("Could not log pipeline failure for %s", phone_number)
        return None


def get_invoice_history(
    phone_number: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Retrieve a user's invoice history ordered by most recent first.

    Args:
        phone_number: E.164-formatted phone number.
        limit:        Maximum number of records to return (default 20).

    Returns:
        A list of invoice dicts, newest first.
    """

    client = get_supabase_client()
    normalized_phone = _normalize_phone(phone_number)

    try:
        response = (
            client.table("invoices")
            .select("*")
            .eq("phone_number", normalized_phone)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        raise SupabaseClientError(
            f"Failed to fetch invoice history for {normalized_phone}"
        ) from exc

    rows = response.data or []
    logger.info(
        "Fetched %d invoice(s) for %s", len(rows), normalized_phone
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Storage operations
# ---------------------------------------------------------------------------

def upload_invoice_pdf(pdf_path: str | Path, invoice_number: str) -> str:
    """Upload a generated invoice PDF to Supabase Storage and return its public URL.

    The bucket name defaults to ``invoices`` and must be created in the Supabase
    dashboard with public read access enabled.

    Args:
        pdf_path:       Local path to the generated PDF file.
        invoice_number: Used as the storage object name (e.g. DROPINV-202506-0001).

    Returns:
        The public HTTPS URL for the uploaded PDF.
    """

    client = get_supabase_client()
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "invoices")
    storage_key = f"{invoice_number}.pdf"

    with Path(pdf_path).open("rb") as file_handle:
        pdf_bytes = file_handle.read()

    try:
        client.storage.from_(bucket).upload(
            path=storage_key,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
    except Exception as exc:
        raise SupabaseClientError(
            f"Failed to upload PDF {storage_key} to bucket '{bucket}'"
        ) from exc

    public_url: str = client.storage.from_(bucket).get_public_url(storage_key)
    logger.info("Uploaded invoice PDF to %s", public_url)
    return public_url


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_phone(phone: str) -> str:
    """Strip the ``whatsapp:`` prefix and ensure an E.164-like format."""

    cleaned = str(phone or "").strip()
    # Twilio prefixes WhatsApp numbers with "whatsapp:"
    if cleaned.lower().startswith("whatsapp:"):
        cleaned = cleaned[len("whatsapp:"):]

    cleaned = cleaned.strip()
    # Ensure Indian numbers start with +91
    if cleaned.startswith("91") and not cleaned.startswith("+"):
        cleaned = f"+{cleaned}"

    return cleaned


def _safe_float(value: Any) -> float | None:
    """Convert a value to float or return None for null-safe DB inserts."""

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None
