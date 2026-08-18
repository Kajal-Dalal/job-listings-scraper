"""
Input validation and parsing utilities.

Shared helpers used by the normalizer and other components:
- strip_html        : remove HTML tags from a string
- truncate          : cap string to max length
- parse_salary      : parse a salary string into (min, max, currency)
- is_valid_url      : basic URL validation
"""
import re
from html.parser import HTMLParser
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Minimal HTML parser that accumulates text nodes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def strip_html(text: str) -> str:
    """
    Remove HTML tags from *text* and return plain text.

    Also collapses multiple whitespace sequences into a single space.

    >>> strip_html("<p>Hello <b>world</b>!</p>")
    'Hello world !'
    """
    if not text:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(text)
    result = stripper.get_text()
    # Collapse whitespace
    return re.sub(r"\s+", " ", result).strip()


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def truncate(text: str, max_length: int = 5000, suffix: str = "…") -> str:
    """
    Truncate *text* to *max_length* characters.

    Appends *suffix* if truncation occurs.

    >>> truncate("hello world", max_length=5)
    'hello…'
    """
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


# ---------------------------------------------------------------------------
# Salary parsing
# ---------------------------------------------------------------------------

# Salary currency symbols → ISO codes
_CURRENCY_MAP = {
    "$": "USD",
    "£": "GBP",
    "€": "EUR",
    "₹": "INR",
    "¥": "JPY",
    "A$": "AUD",
    "C$": "CAD",
}

# Patterns to detect "per year" vs "per hour" modifiers
_HOURLY_RE = re.compile(r"\b(?:per\s+hour|/hr|/hour|hourly)\b", re.IGNORECASE)
_YEARLY_RE = re.compile(r"\b(?:per\s+year|/yr|/year|annually|pa|p\.a\.)\b", re.IGNORECASE)
_LPA_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*LPA\b", re.IGNORECASE)  # Indian lakh per annum

# Main salary range regex:  "$120k - $160k"  or  "120,000 to 160,000"
_RANGE_RE = re.compile(
    r"""
    (?P<curr1>[£€₹¥]|A\$|C\$|\$)?   # optional leading currency
    \s*
    (?P<min>[\d,]+(?:\.\d+)?)         # min value
    \s*[kK]?                           # optional k suffix
    \s*[-–—to/]+                       # separator
    \s*
    (?P<curr2>[£€₹¥]|A\$|C\$|\$)?   # optional currency before max
    \s*
    (?P<max>[\d,]+(?:\.\d+)?)         # max value
    \s*[kK]?                           # optional k suffix
    """,
    re.VERBOSE,
)

# Single value: "$80k", "80000"
_SINGLE_RE = re.compile(
    r"""
    (?P<curr>[£€₹¥]|A\$|C\$|\$)?
    \s*
    (?P<val>[\d,]+(?:\.\d+)?)
    \s*[kK]?
    """,
    re.VERBOSE,
)


def _parse_number(raw: str, has_k_suffix: bool) -> Optional[int]:
    """Parse a number string, handling commas and K suffix."""
    try:
        num = float(raw.replace(",", ""))
        if has_k_suffix or (num < 1000 and num >= 10):
            num *= 1000
        return int(num)
    except (ValueError, TypeError):
        return None


def parse_salary(raw: str) -> Tuple[Optional[int], Optional[int], str]:
    """
    Parse a freeform salary string into (min, max, currency_code).

    Handles formats:
    - "$120,000 - $160,000"
    - "£50k – £80k"
    - "80000 to 120000"
    - "15 LPA"  (Indian lakh per annum → multiplied by 100,000)
    - "$50/hr"  (hourly → annualised at 2080 hrs)

    Returns:
        (salary_min, salary_max, currency_code)
        Values are None if not parseable.
    """
    if not raw:
        return None, None, "USD"

    raw = raw.strip()
    currency = "USD"

    # --- LPA (Indian) ---
    lpa_m = _LPA_RE.search(raw)
    if lpa_m:
        val_lakhs = float(lpa_m.group(1))
        val = int(val_lakhs * 100_000)
        return val, val, "INR"

    # --- Detect currency ---
    for symbol, code in _CURRENCY_MAP.items():
        if symbol in raw:
            currency = code
            break

    # --- Detect if hourly ---
    is_hourly = bool(_HOURLY_RE.search(raw))

    # --- Range match ---
    m = _RANGE_RE.search(raw)
    if m:
        raw_text = m.group(0)
        has_k = "k" in raw_text.lower()
        min_val = _parse_number(m.group("min"), has_k)
        max_val = _parse_number(m.group("max"), has_k)

        if min_val and max_val:
            if is_hourly:
                min_val = int(min_val * 2080)
                max_val = int(max_val * 2080)
            # Sanity check: swap if inverted
            if min_val > max_val:
                min_val, max_val = max_val, min_val
            return min_val, max_val, currency

    # --- Single value ---
    s = _SINGLE_RE.search(raw)
    if s:
        has_k = "k" in raw.lower()
        val = _parse_number(s.group("val"), has_k)
        if val:
            if is_hourly:
                val = int(val * 2080)
            return val, val, currency

    return None, None, currency


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

_URL_RE = re.compile(
    r"^https?://"               # scheme
    r"[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+"  # rest
    r"$",
    re.IGNORECASE,
)


def is_valid_url(url: str) -> bool:
    """
    Returns True if *url* looks like a valid HTTP/HTTPS URL.

    >>> is_valid_url("https://example.com/jobs/1")
    True
    >>> is_valid_url("not a url")
    False
    """
    if not url or not isinstance(url, str):
        return False
    return bool(_URL_RE.match(url.strip()))
