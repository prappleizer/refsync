"""
Parsing helpers for NASA ADS URLs and bibcodes.

A bibcode is a 19-character identifier with the structure:
    YYYYJJJJJVVVVMPPPPA
    year | journal | volume | qualifier | page | author-initial

We don't rigidly validate that structure (ADS occasionally bends it), but we
use the length + leading-4-digit-year as a sanity check.
"""

import re
from urllib.parse import unquote

# ADS "abstract" URLs look like:
#   https://ui.adsabs.harvard.edu/abs/2025ApJ...980L...3P/abstract
#   https://ui.adsabs.harvard.edu/abs/2025ApJ...980L...3P
#   https://ui.adsabs.harvard.edu/abs/arXiv:2607.01832/abstract
#   https://ui.adsabs.harvard.edu/link_gateway/2025ApJ...980L...3P/EPRINT_PDF
_ADS_ABS_RE = re.compile(
    r"adsabs\.harvard\.edu/(?:abs|link_gateway)/([^/\s]+)",
    re.IGNORECASE,
)

# A bibcode is 19 chars, begins with a 4-digit year. This is a loose check used
# to decide whether a raw pasted token is a bibcode.
_BIBCODE_RE = re.compile(r"^\d{4}[A-Za-z0-9.&+]{15}$")


def parse_ads_bibcode(url_or_id: str) -> str | None:
    """
    Extract an ADS bibcode from any ADS URL form, or from a raw pasted bibcode.

    Returns the (URL-decoded) bibcode string, or None if nothing looks like one.
    Note: the returned token may be an 'arXiv:...' pseudo-identifier when the
    ADS record is arxiv-only; callers should check for that and route to arxiv.
    """
    token = url_or_id.strip()

    # Case 1: it's an ADS URL — pull the path segment after /abs/ or /link_gateway/
    m = _ADS_ABS_RE.search(token)
    if m:
        candidate = unquote(m.group(1))
        # Strip a trailing "/abstract" if the regex somehow kept it (it won't,
        # since [^/]+ stops at the slash, but be defensive against odd inputs).
        candidate = candidate.split("/")[0]
        return candidate

    # Case 2: raw pasted "arXiv:...." pseudo-bibcode
    if token.lower().startswith("arxiv:"):
        return token

    # Case 3: raw pasted bibcode (19 chars, year-leading)
    decoded = unquote(token)
    if _BIBCODE_RE.match(decoded):
        return decoded

    return None


def bibcode_is_arxiv(bibcode: str) -> str | None:
    """
    If a bibcode/identifier is really an arXiv pointer (e.g. 'arXiv:2607.01832'
    or the '2607.01832' form), return the bare arXiv id. Otherwise None.

    This covers the case where someone pastes an ADS link for a paper that ADS
    only knows as an eprint, so we can route through the richer arxiv fetch.
    """
    b = bibcode.strip()
    if b.lower().startswith("arxiv:"):
        return b.split(":", 1)[1]
    # Some arxiv-only bibcodes look like "2024arXiv240712345S"
    m = re.search(r"arxiv(\d{4}\.\d{4,5}|\d{7})", b, re.IGNORECASE)
    if m:
        raw = m.group(1)
        # Old-style like 240712345 -> can't reliably reconstruct; leave to ADS.
        if "." in raw:
            return raw
    return None


def ads_eprint_pdf_url(bibcode: str) -> str:
    """
    Build the ADS link-gateway URL that 302-redirects to the best available PDF
    (arxiv eprint when present). We store the gateway URL and let ADS forward.
    """
    from urllib.parse import quote

    return f"https://ui.adsabs.harvard.edu/link_gateway/{quote(bibcode)}/EPRINT_PDF"


def ads_abstract_url(bibcode: str) -> str:
    """Canonical ADS abstract page for a bibcode."""
    from urllib.parse import quote

    return f"https://ui.adsabs.harvard.edu/abs/{quote(bibcode)}/abstract"
