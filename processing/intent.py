"""Request intent detection for DropInvoice.

Decides, from the customer's message, what kind of document to produce and which
options to apply — without getting sales bills and salary slips jumbled. Used by
the hybrid flow: auto-detect when the signal is clear, ask a menu only when it is
genuinely ambiguous.

Three independent decisions are detected:
- doc_type : "sales" (goods/GST invoice) or "salary" (payslip), or None if unclear.
- gst      : True (apply GST), False (non-GST bill), or None if not specified.
- tally    : True (post to Tally), False (do not), or None if not specified.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Explicit hashtag-style flags the user can put anywhere in the message.
FLAG_PATTERNS: dict[str, tuple[str, ...]] = {
    "salary": (r"#\s*salary", r"#\s*payslip", r"#\s*payroll"),
    "sales": (r"#\s*sales", r"#\s*invoice", r"#\s*bill"),
    "gst": (r"#\s*gst\b",),
    "nogst": (r"#\s*no\s*gst", r"#\s*nogst", r"#\s*non[\s-]*gst"),
    "tally": (r"#\s*tally",),
    "notally": (r"#\s*no\s*tally", r"#\s*notally"),
}

# Natural-language signals (no hashtag needed).
SALARY_WORDS = re.compile(
    r"\b(salary|salaries|payslip|pay\s*slip|payroll|wages?|tankhwa|tankhuah|"
    r"vetan|tnkhwa|net\s*pay|basic\s*pay|hra|provident|\bpf\b|deduction)\b",
    re.I,
)
NON_GST_WORDS = re.compile(
    r"\b(no\s*gst|without\s*gst|non[\s-]*gst|gst\s*free|bina\s*gst|gst\s*nahi|"
    r"kacc?ha\s*bill|cash\s*bill)\b",
    re.I,
)
GST_WORDS = re.compile(r"\b(with\s*gst|gst\s*bill|pakka\s*bill|add\s*gst)\b", re.I)
TALLY_WORDS = re.compile(r"\b(tally|post\s*to\s*tally|update\s*tally)\b", re.I)


@dataclass(frozen=True)
class RequestOptions:
    """Detected options for a bill request. None means 'not specified'."""

    doc_type: str | None = None   # "sales" | "salary" | None
    gst: bool | None = None       # True | False | None
    tally: bool | None = None     # True | False | None

    def is_complete(self) -> bool:
        """True when doc_type and gst are both known (Tally has a safe default)."""

        return self.doc_type is not None and self.gst is not None


def _has_flag(text: str, key: str) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in FLAG_PATTERNS[key])


def detect_options(text: str) -> RequestOptions:
    """Detect document type and GST/Tally options from a message. Never raises."""

    body = str(text or "")

    # Document type: explicit flag wins, then natural-language salary words.
    doc_type: str | None = None
    if _has_flag(body, "salary"):
        doc_type = "salary"
    elif _has_flag(body, "sales"):
        doc_type = "sales"
    elif SALARY_WORDS.search(body):
        doc_type = "salary"

    # GST: explicit flags first, then natural language.
    gst: bool | None = None
    if _has_flag(body, "nogst") or NON_GST_WORDS.search(body):
        gst = False
    elif _has_flag(body, "gst") or GST_WORDS.search(body):
        gst = True

    # Tally: explicit only (don't post unless asked or globally enabled elsewhere).
    tally: bool | None = None
    if _has_flag(body, "notally"):
        tally = False
    elif _has_flag(body, "tally") or TALLY_WORDS.search(body):
        tally = True

    return RequestOptions(doc_type=doc_type, gst=gst, tally=tally)


def strip_flags(text: str) -> str:
    """Remove #flags from the message so they aren't parsed as line items."""

    cleaned = re.sub(r"#\s*[A-Za-z]+", " ", str(text or ""))
    return re.sub(r"[ \t]+", " ", cleaned).strip()


# Signals that a message clearly describes goods (so it's a sales bill, not an
# ambiguous name list). Units, "at/@ <price>", "<qty> x", or product keywords.
GOODS_SIGNAL = re.compile(
    r"\b(\d+\s*(kg|kgs|gm|gms|g|ltr|litre|liter|ml|mtr|metre|meter|pcs|pc|nos|"
    r"dozen|doz|box|packet|pkt|bag|bottle)\b|at\s*\d|x\s*\d|\d+\s*@)",
    re.I,
)


def has_goods_signal(text: str) -> bool:
    """Return True when the text clearly describes purchasable goods."""

    body = str(text or "")
    if GOODS_SIGNAL.search(body):
        return True
    # Fall back to product keywords from the GST engine.
    from invoice.gst_rates import KEYWORD_RATES

    lowered = body.lower()
    return any(keyword in lowered for keyword in KEYWORD_RATES)


def needs_menu(text: str, options: RequestOptions) -> bool:
    """Return True when the request is ambiguous (used by the 'ambiguous' mode).

    A clear salary/sales signal, or any goods signal, resolves without asking.
    """

    if options.doc_type is not None:
        return False
    return not has_goods_signal(text)


def has_any_flag(text: str) -> bool:
    """Return True when the message contains any explicit #flag the user set."""

    return any(_has_flag(text, key) for key in FLAG_PATTERNS)


def menu_mode() -> str:
    """Return the configured menu mode: 'always' (default), 'ambiguous', or 'off'."""

    mode = os.getenv("MENU_MODE", "always").strip().lower()
    return mode if mode in {"always", "ambiguous", "off"} else "always"


def should_show_menu(text: str, options: RequestOptions, media_kind: str) -> bool:
    """Decide whether to ask the 3-question menu before generating.

    - 'off'       : never ask (pure auto-detect).
    - 'always'    : ask for every bill, UNLESS the user already drove it with
                    explicit #flags (then respect their flags and skip).
    - 'ambiguous' : ask only when a TEXT message is genuinely ambiguous; images
                    and clear messages proceed with smart defaults.
    """

    mode = menu_mode()
    if mode == "off":
        return False
    if has_any_flag(text):
        return False
    if mode == "always":
        return True
    # 'ambiguous' mode
    if media_kind != "text":
        return False
    return needs_menu(text, options)


MENU_MESSAGE = (
    "Got it! How should I prepare this? Reply with 3 numbers, e.g. *1 2 1*\n\n"
    "1) Bill type — 1=GST bill, 2=Non-GST bill\n"
    "2) Post to Tally — 1=Yes, 2=No\n"
    "3) Document — 1=Sales invoice, 2=Salary slip"
)


@dataclass(frozen=True)
class MenuChoices:
    """Parsed answers from the 3-question menu."""

    gst: bool
    tally: bool
    doc_type: str


def parse_menu_reply(text: str) -> MenuChoices | None:
    """Parse a menu reply like '1 2 1' or 'gst no salary' into choices, or None.

    Must resolve to exactly three valid answers, otherwise it is not treated as
    a menu reply (so a normal bill is never mistaken for one).
    """

    tokens = re.split(r"[\s,/|]+", str(text or "").strip().lower())
    tokens = [token for token in tokens if token]
    if len(tokens) != 3:
        return None

    gst = _map_token(tokens[0], {"1": True, "gst": True, "yes": True,
                                 "2": False, "nogst": False, "non-gst": False, "no": False})
    tally = _map_token(tokens[1], {"1": True, "yes": True, "tally": True,
                                   "2": False, "no": False, "notally": False})
    doc = _map_token(tokens[2], {"1": "sales", "sales": "sales", "invoice": "sales",
                                 "2": "salary", "salary": "salary", "payslip": "salary"})
    if gst is None or tally is None or doc is None:
        return None
    return MenuChoices(gst=gst, tally=tally, doc_type=doc)


def _map_token(token: str, mapping: dict[str, Any]) -> Any:
    return mapping.get(token)
