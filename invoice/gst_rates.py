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

# Known person/individual names whose transactions are NOT taxable (labour,
# salary, or personal payments — not a supply of goods). Matched case-insensitively
# as whole words. Extend this list as needed.
PERSON_NAMES: frozenset[str] = frozenset({
    "anita", "javed", "pankaj", "payal", "sushant",
})

# Words that mark a line as a personal/financial transaction rather than a sale of
# goods — these are NOT taxable, so no GST applies. Includes common Hinglish terms.
NON_TAXABLE_KEYWORDS: frozenset[str] = frozenset({
    "payment", "paid", "received", "salary", "wages", "wage", "labour", "labor",
    "majdoori", "mazdoori", "advance", "loan", "udhaar", "udhar", "rent", "kiraya",
    "deposit", "transfer", "withdraw", "withdrawal", "cash", "borrow", "lend",
    "given", "gave", "personal", "gift", "donation", "refund", "fees", "fee",
    "commission", "tip", "bonus", "reimbursement", "settlement", "emi",
})


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
# Flat keyword -> rate map, matched case-insensitively against the item
# description. When several keywords match, the LONGEST (most specific) wins —
# so "Mong Dal Papad" resolves via "papad" (5%), not "dal" (0%). Includes common
# Hinglish terms so rough Indian bills resolve sensibly.
KEYWORD_RATES: dict[str, float] = {
    # 0% — fresh produce, unbranded staples, basic necessities
    "fresh vegetable": 0.0, "vegetable": 0.0, "sabzi": 0.0, "subzi": 0.0,
    "fresh fruit": 0.0, "fruit": 0.0, "milk": 0.0, "doodh": 0.0, "dahi": 0.0,
    "curd": 0.0, "egg": 0.0, "anda": 0.0, "bread": 0.0, "salt": 0.0, "namak": 0.0,
    "atta": 0.0, "aata": 0.0, "wheat flour": 0.0, "rice": 0.0, "chawal": 0.0,
    "dal": 0.0, "pulse": 0.0, "lentil": 0.0, "book": 0.0, "newspaper": 0.0,
    "jaggery": 0.0, "gud": 0.0, "gur": 0.0, "besan": 0.0, "maida": 0.0,
    "sooji": 0.0, "rava": 0.0, "poha": 0.0, "kumkum": 0.0, "bindi": 0.0,
    # 5% — packaged staples, edible oil, spices, tea/coffee, footwear, medicines
    "sugar": 5.0, "cheeni": 5.0, "chini": 5.0, "tea": 5.0, "chai": 5.0,
    "coffee": 5.0, "edible oil": 5.0, "cooking oil": 5.0, "oil": 5.0, "tel": 5.0,
    "spice": 5.0, "masala": 5.0, "haldi": 5.0, "turmeric": 5.0, "mirch": 5.0,
    "chilli": 5.0, "dhaniya": 5.0, "coriander": 5.0, "jeera": 5.0, "cumin": 5.0,
    "papad": 5.0, "paneer": 5.0, "fertilizer": 5.0, "khaad": 5.0, "coal": 5.0,
    "footwear": 5.0, "chappal": 5.0, "slipper": 5.0, "sandal": 5.0,
    "medicine": 5.0, "dawai": 5.0, "tablet": 5.0, "syrup": 5.0, "agarbatti": 5.0,
    "incense": 5.0, "matchbox": 5.0, "kerosene": 5.0,
    # 12% — dairy fats, dry fruits, processed/packaged food, mobiles
    "ghee": 12.0, "butter": 12.0, "makhan": 12.0, "cheese": 12.0,
    "almond": 12.0, "badam": 12.0, "walnut": 12.0, "akhroot": 12.0,
    "cashew": 12.0, "kaju": 12.0, "raisin": 12.0, "kishmish": 12.0,
    "dry fruit": 12.0, "namkeen": 12.0, "juice": 12.0, "frozen": 12.0,
    "umbrella": 12.0, "sewing machine": 12.0, "mobile": 12.0, "feeding bottle": 12.0,
    "candle": 12.0, "spectacle": 12.0, "tooth powder": 12.0,
    # 18% — toiletries, packaged snacks, electronics, stationery, services
    "soap": 18.0, "sabun": 18.0, "shampoo": 18.0, "toothpaste": 18.0,
    "detergent": 18.0, "surf": 18.0, "hair oil": 18.0, "biscuit": 18.0,
    "cake": 18.0, "pasta": 18.0, "noodle": 18.0, "maggi": 18.0, "sauce": 18.0,
    "ketchup": 18.0, "jam": 18.0, "ice cream": 18.0, "chocolate": 18.0,
    "shoe": 18.0, "battery": 18.0, "charger": 18.0, "cable": 18.0, "bulb": 18.0,
    "led": 18.0, "fan": 18.0, "electronic": 18.0, "printer": 18.0, "camera": 18.0,
    "stationery": 18.0, "pen": 18.0, "notebook": 18.0, "register": 18.0,
    "shirt": 18.0, "trouser": 18.0, "utensil": 18.0, "steel": 18.0, "plastic": 18.0,
    "pipe": 18.0, "wire": 18.0, "hardware": 18.0,
    # 28% — large appliances, construction, vehicles, sin/luxury goods
    "air conditioner": 28.0, "refrigerator": 28.0, "fridge": 28.0,
    "television": 28.0, "cement": 28.0, "car": 28.0, "bike": 28.0,
    "motorcycle": 28.0, "scooter": 28.0, "tobacco": 28.0, "cigarette": 28.0,
    "paan masala": 28.0, "gutkha": 28.0, "aerated": 28.0, "cold drink": 28.0,
    "soft drink": 28.0, "soda": 28.0, "perfume": 28.0, "paint": 28.0,
    "marble": 28.0, "tile": 28.0, "washing machine": 28.0, "dishwasher": 28.0,
}


def is_non_taxable(description: Any) -> bool:
    """Return True when a line is a personal/financial transaction, not a good.

    Personal payments (labour, salary, loans, rent, money to/from a named person)
    are not a taxable supply, so no GST applies. Matches the known PERSON_NAMES
    and NON_TAXABLE_KEYWORDS as whole words, case-insensitively.
    """

    words = re.findall(r"[a-z]+", str(description or "").lower())
    return any(word in PERSON_NAMES or word in NON_TAXABLE_KEYWORDS for word in words)


# Backwards-compatible alias.
def is_person_transaction(description: Any) -> bool:
    """Deprecated: use is_non_taxable. Kept for compatibility."""

    return is_non_taxable(description)


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
    """Resolve a GST rate from item description keywords, or None.

    When several keywords match, the longest (most specific) one wins so that,
    e.g., "papad" beats "dal" in "Mong Dal Papad".
    """

    text = str(description or "").lower()
    matches = [keyword for keyword in KEYWORD_RATES if keyword in text]
    if not matches:
        return None

    best = max(matches, key=len)
    return KEYWORD_RATES[best]


def resolve_item_gst_rate(
    hsn_code: Any = None,
    description: Any = None,
    suggested_rate: Any = None,
) -> float:
    """Return a valid GST slab for an item — never raises.

    Resolution order (most authoritative first):
      0. Personal/financial transactions -> 0 (no GST).
      1. A real HSN code printed on the bill, mapped to a known rate.
      2. A rate suggested by the model, if it is a valid slab.
      3. The item description matched against category keywords.
      4. The default standard rate (18%).
    The result is always clamped to a legal slab.
    """

    # 0. Personal/financial transactions are never taxed — overrides everything.
    if is_non_taxable(description):
        return 0.0

    # 1. A real HSN code on the bill is authoritative (generic "9999" maps to None).
    hsn_rate = rate_from_hsn(hsn_code)
    if hsn_rate is not None:
        return hsn_rate

    # 2. Trust the model's suggestion when it is already a legal slab.
    try:
        if suggested_rate is not None:
            suggested = float(suggested_rate)
            if suggested in VALID_GST_SLABS:
                return suggested
    except (TypeError, ValueError):
        pass

    # 3. Description keyword lookup (longest, most specific match wins).
    keyword_rate = rate_from_description(description)
    if keyword_rate is not None:
        return keyword_rate

    # 4. Safe default.
    return DEFAULT_GST_RATE
