"""Embedded Indian GST rate engine for DropInvoice.

This module makes GST resolution self-contained and crash-proof. Given an item's
HSN code and/or description (and optionally a rate suggested by the vision model),
it always returns a valid legal GST slab — never raising.

Coverage & honesty:
- The legal GST slabs are 0, 5, 12, 18, and 28 percent (plus special cases like
  3% for gold and cess on sin/luxury goods which a prototype does not need).
- India has ~12,000 HSN codes and rates change by government notification, so a
  fully authoritative mapping would require the official live rate finder. This
  module embeds the standard slabs plus common retail-category mappings and
  always falls back to 18% (the most common standard rate) when unsure.
- For accuracy on a specific item, the vision model is asked to suggest a rate;
  this engine then validates/clamps that suggestion to a legal slab.
"""

from __future__ import annotations

import re
from typing import Any

# The only legal GST slabs this prototype handles.
VALID_GST_SLABS: tuple[float, ...] = (0.0, 5.0, 12.0, 18.0, 28.0)

# Fallback when nothing else resolves — 18% is the most common standard rate.
DEFAULT_GST_RATE: float = 18.0


# ---------------------------------------------------------------------------
# HSN-code → rate rules
# ---------------------------------------------------------------------------
# Matched by LONGEST prefix first, so a 4-digit rule overrides a 2-digit chapter.
# Values are the most common GST rate for that HSN family in Indian retail.
HSN_PREFIX_RATES: dict[str, float] = {
    # Chapter 01-05: live animals, meat, fish, dairy, eggs, honey
    "02": 0.0, "03": 0.0, "0401": 0.0, "0407": 0.0, "04062000": 0.0,
    "0406": 12.0,            # cheese / paneer (packaged)
    "0405": 12.0,            # butter
    "04059020": 12.0,        # ghee
    # Chapter 07-08: vegetables, fruits, nuts
    "07": 0.0,               # fresh vegetables
    "08": 0.0,               # fresh fruits
    "0801": 12.0, "0802": 12.0,   # dried/packaged nuts (almond, walnut, cashew)
    "0813": 12.0,            # dried fruit mix
    # Chapter 09: spices, tea, coffee
    "0901": 5.0, "0902": 5.0, "0904": 5.0, "0909": 5.0, "0910": 5.0,
    # Chapter 10-11: cereals & milling (branded/packaged taxed at 5%)
    "10": 5.0, "1101": 5.0, "1102": 5.0, "1106": 5.0,
    # Chapter 15: edible oils & fats
    "15": 5.0,
    # Chapter 17: sugar (5%) and sugar confectionery (18%)
    "1701": 5.0, "1704": 18.0,
    # Chapter 18: cocoa / chocolate
    "1806": 18.0,
    # Chapter 19: bakery, cereal preparations, papad, namkeen
    "1905": 18.0, "19059040": 5.0,   # papad is 5%
    "1902": 18.0,            # pasta / noodles
    # Chapter 20-21: preserved food, sauces, namkeen, ice cream
    "20": 12.0, "2106": 18.0, "21050000": 18.0,   # ice cream 18%
    # Chapter 22: beverages
    "2201": 18.0, "2202": 28.0,   # aerated/sweetened drinks 28%
    # Chapter 24: tobacco
    "24": 28.0,
    # Chapter 25: salt (0%) and cement (28%)
    "2501": 0.0, "2523": 28.0,
    # Chapter 30: pharmaceuticals
    "30": 12.0,
    # Chapter 33-34: cosmetics, perfume, soap, detergent
    "3303": 18.0, "3304": 18.0, "3305": 18.0, "3307": 18.0,
    "3401": 18.0, "3402": 18.0,
    # Chapter 39: plastics / household plastic
    "39": 18.0,
    # Chapter 48-49: paper (12-18%), books & newspapers (0%)
    "48": 18.0, "4901": 0.0, "4902": 0.0,
    # Chapter 61-62: apparel (5% if low value, else 12% — default 5%)
    "61": 5.0, "62": 5.0,
    # Chapter 64: footwear
    "64": 18.0,
    # Chapter 84-85: machinery & electronics
    "84": 18.0, "85": 18.0,
    "8415": 28.0,            # air conditioners
    "8418": 28.0,            # refrigerators
    "8528": 28.0,            # large televisions / monitors
    "8517": 18.0,            # mobile phones
    # Chapter 87: vehicles
    "87": 28.0,
    # Chapter 94: furniture & lighting
    "94": 18.0,
}


# ---------------------------------------------------------------------------
# Description-keyword → rate rules (for handwritten bills with no HSN code)
# ---------------------------------------------------------------------------
# Matched case-insensitively against the item description. Includes common
# Hinglish terms so rough Indian bills resolve sensibly.
KEYWORD_RATES: tuple[tuple[float, tuple[str, ...]], ...] = (
    (0.0, (
        "fresh vegetable", "sabzi", "subzi", "vegetable", "fruit", "milk",
        "doodh", "dahi", "curd", "egg", "anda", "bread", "salt", "namak",
        "atta", "aata", "wheat flour", "besan flour", "rice", "chawal",
        "dal", "pulse", "book", "newspaper", "jaggery", "gud",
    )),
    (5.0, (
        "sugar", "cheeni", "chini", "tea", "chai", "coffee", "oil", "tel",
        "ghee substitute", "spice", "masala", "haldi", "mirch", "dhaniya",
        "jeera", "papad", "paneer", "skimmed milk", "fertilizer", "coal",
        "footwear", "chappal", "slipper",
    )),
    (12.0, (
        "ghee", "butter", "makhan", "cheese", "almond", "badam", "walnut",
        "akhroot", "cashew", "kaju", "dry fruit", "namkeen", "juice",
        "frozen", "umbrella", "sewing", "mobile",
    )),
    (18.0, (
        "soap", "sabun", "shampoo", "toothpaste", "detergent", "surf",
        "hair oil", "biscuit", "cake", "pasta", "noodle", "maggi", "sauce",
        "ketchup", "ice cream", "chocolate", "shoe", "battery", "charger",
        "electronic", "printer", "camera", "stationery", "pen",
    )),
    (28.0, (
        "ac ", "air conditioner", "refrigerator", "fridge", "television",
        "cement", "car", "bike", "motorcycle", "tobacco", "cigarette",
        "paan masala", "aerated", "cold drink", "soft drink", "perfume",
        "paint", "luxury",
    )),
)


def clamp_to_slab(rate: Any) -> float:
    """Snap any numeric rate to the nearest valid legal GST slab.

    Always returns one of VALID_GST_SLABS; returns DEFAULT_GST_RATE for
    non-numeric input. Never raises.
    """

    try:
        value = float(rate)
    except (TypeError, ValueError):
        return DEFAULT_GST_RATE

    if value < 0:
        return DEFAULT_GST_RATE

    if value in VALID_GST_SLABS:
        return value

    return min(VALID_GST_SLABS, key=lambda slab: abs(slab - value))


def rate_from_hsn(hsn_code: Any) -> float | None:
    """Resolve a GST rate from an HSN code by longest-prefix match, or None."""

    digits = re.sub(r"\D", "", str(hsn_code or ""))
    if not digits:
        return None

    for length in range(len(digits), 1, -1):
        prefix = digits[:length]
        if prefix in HSN_PREFIX_RATES:
            return HSN_PREFIX_RATES[prefix]

    return None


def rate_from_description(description: Any) -> float | None:
    """Resolve a GST rate from item description keywords, or None."""

    text = f" {str(description or '').lower()} "
    for rate, keywords in KEYWORD_RATES:
        for keyword in keywords:
            if keyword in text:
                return rate

    return None


def resolve_item_gst_rate(
    hsn_code: Any = None,
    description: Any = None,
    suggested_rate: Any = None,
) -> float:
    """Return a valid GST slab for an item — never raises.

    Resolution order (most authoritative first):
      1. A rate suggested by the vision model, if it is a valid slab.
      2. The item's HSN code mapped to a known rate.
      3. The item description matched against category keywords.
      4. The default standard rate (18%).
    The result is always clamped to a legal slab.
    """

    # 1. Trust an explicit suggestion only when it is already a legal slab.
    try:
        if suggested_rate is not None:
            suggested = float(suggested_rate)
            if suggested in VALID_GST_SLABS:
                return suggested
    except (TypeError, ValueError):
        pass

    # 2. HSN code lookup.
    hsn_rate = rate_from_hsn(hsn_code)
    if hsn_rate is not None:
        return hsn_rate

    # 3. Description keyword lookup.
    keyword_rate = rate_from_description(description)
    if keyword_rate is not None:
        return keyword_rate

    # 4. Safe default.
    return DEFAULT_GST_RATE
