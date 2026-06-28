"""In-memory store of pending bill requests awaiting a menu answer.

When a message is ambiguous, DropInvoice asks the customer a short menu (GST?
Tally? Sales or Salary?) and parks the request here until they reply. Keyed by
phone number. This is intentionally in-memory for the prototype — pending state
is lost on restart, which is acceptable (the customer simply re-sends).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

PENDING_TTL_SECONDS = 600  # a parked request expires after 10 minutes

_lock = threading.Lock()
_pending: dict[str, "PendingRequest"] = {}


@dataclass
class PendingRequest:
    """A bill request waiting for the customer's menu choices."""

    from_number: str
    media_kind: str                       # "text" | "image" | "audio"
    body: str = ""                        # original message text (for text/captions)
    media_path: str = ""                  # downloaded media path (for image/audio)
    metadata: dict[str, Any] = field(default_factory=dict)
    incoming: Any = None                  # original IncomingMessage (for media re-download)
    created_at: float = 0.0


def set_pending(request: PendingRequest, now: float) -> None:
    """Park a request for the given phone number, replacing any earlier one."""

    request.created_at = now
    with _lock:
        _pending[request.from_number] = request


def get_pending(from_number: str, now: float) -> PendingRequest | None:
    """Return a non-expired pending request for the number, or None."""

    with _lock:
        request = _pending.get(from_number)
        if request is None:
            return None
        if now - request.created_at > PENDING_TTL_SECONDS:
            _pending.pop(from_number, None)
            return None
        return request


def clear_pending(from_number: str) -> None:
    """Remove any pending request for the number."""

    with _lock:
        _pending.pop(from_number, None)
