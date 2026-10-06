"""
BibTeX generation and management service.
"""

import re
import unicodedata
from typing import Optional

from ..models import Paper


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md"}

# Letters that don't decompose into base letter + accent under NFKD
_ASCII_EXTRA = str.maketrans(
    {
        "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D",
        "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "ı": "i", "ħ": "h",
        "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    }
)


def _ascii_fold(text: str) -> str:
    """Strip accents so cite keys are plain ASCII (Kereš -> Keres, Ø -> O)."""
    text = text.translate(_ASCII_EXTRA)
    decomposed = unicodedata.normalize("NFKD", text)
    return decomposed.encode("ascii", "ignore").decode("ascii")


def make_cite_key_name(last_name: str) -> str:
    """
    Turn a surname into the name part of a cite key: ASCII only, spaces as
    underscores, nothing but letters, digits, '_' and '-'.
    "van Dokkum" -> "van_Dokkum", "Faucher-Giguère" -> "Faucher-Giguere".
    """
    name = _ascii_fold(last_name.strip())
    name = re.sub(r"\s+", "_", name)
    return re.sub(r"[^A-Za-z0-9_-]", "", name)


def _paper_year(paper: Paper) -> Optional[int]:
    """Best-effort publication year: from `published`, else None."""
    if paper.published:
        return paper.published.year
    return None


def _split_name(name: str) -> tuple[str, str]:
    """
    Split an author name into (last, first).

    The last name keeps any lowercase particles, e.g. "van Dokkum",
    "de la Cruz", "van der Wel". Accepts "First Last" or "Last, First".
    """
    name = name.strip()
    if "," in name:
        last, _, first = name.partition(",")
        return last.strip(), first.strip()

    parts = name.split()
    if not parts:
        return "Unknown", ""

    # Drop trailing suffixes (Jr., III, ...)
    end = len(parts)
    while end > 1 and parts[end - 1].lower().rstrip(".") in _SUFFIXES:
        end -= 1

    # Surname is the last real word, plus any lowercase particles before it
    # (the same rule BibTeX uses for its "von" part)
    start = end - 1
    while start > 0 and parts[start - 1][0].islower():
        start -= 1

    return " ".join(parts[start:end]), " ".join(parts[:start])


def generate_cite_key(paper: Paper, existing_keys: Optional[set[str]] = None) -> str:
    """
    Generate a cite key in format LastName:Year (e.g., McCallum:2025).
    Keys are ASCII-only: multi-word surnames are joined with underscores
    (van_Dokkum:2025) and accents are dropped (Faucher-Giguere:2023).
    Handles duplicates with a, b, c suffixes.

    Args:
        paper: Paper to generate key for
        existing_keys: Set of existing cite keys to avoid collisions

    Returns:
        Unique cite key string
    """
    existing_keys = existing_keys or set()

    # Extract first author's last name (including particles like "van")
    if paper.authors:
        last_name, _ = _split_name(paper.authors[0])
    else:
        last_name = "Unknown"

    # ASCII only, spaces -> underscores (van Dokkum -> van_Dokkum,
    # Faucher-Giguère -> Faucher-Giguere), so classic BibTeX accepts the key
    last_name = make_cite_key_name(last_name) or "Unknown"

    # Get year from published date (may be missing for sparse ADS records)
    year = _paper_year(paper)
    year_str = str(year) if year is not None else "0000"

    # Base key
    base_key = f"{last_name}:{year_str}"

    # Check for collisions and add suffix if needed
    if base_key not in existing_keys:
        return base_key

    # Try a, b, c, ... suffixes
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = f"{base_key}{suffix}"
        if candidate not in existing_keys:
            return candidate

    # Fallback: append a stable disambiguator from whatever identifier we have.
    tail = paper.arxiv_id or paper.bibcode or paper.id
    tail = tail.replace(".", "_").replace("/", "_")
    return f"{base_key}_{tail}"


def format_authors_bibtex(authors: list[str]) -> str:
    """
    Format author list for BibTeX.
    Converts "First Last" to "{Last}, First" format and joins with " and ".
    Surname particles are kept with the last name ("{van Dokkum}, Pieter").
    """
    formatted = []
    for author in authors:
        last, first = _split_name(author)
        formatted.append(f"{{{last}}}, {first}" if first else f"{{{last}}}")

    return " and ".join(formatted)


def escape_bibtex(text: str) -> str:
    """Escape special characters for BibTeX."""
    # Replace common LaTeX-sensitive characters
    # Note: We preserve existing LaTeX commands
    replacements = [
        ("&", r"\&"),
        ("%", r"\%"),
        ("_", r"\_"),
        ("#", r"\#"),
    ]

    result = text
    for old, new in replacements:
        # Only replace if not already escaped
        result = re.sub(rf"(?<!\\){re.escape(old)}", new, result)

    return result


def generate_arxiv_bibtex(paper: Paper, cite_key: str) -> str:
    """
    Generate BibTeX entry from arXiv paper metadata.

    Only valid for papers that actually have an arXiv id. Callers should not
    invoke this for ADS-only papers (use the ADS-provided BibTeX instead).

    Args:
        paper: Paper object with metadata (must have arxiv_id and published)
        cite_key: Citation key to use

    Returns:
        BibTeX string
    """
    if not paper.arxiv_id:
        raise ValueError("generate_arxiv_bibtex called on a paper without an arxiv_id")

    authors = format_authors_bibtex(paper.authors)
    title = escape_bibtex(paper.title)
    year = _paper_year(paper)

    # Get primary category for primaryClass
    primary_class = paper.categories[0] if paper.categories else "astro-ph"

    # Format month
    month_names = [
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    ]
    month_line = ""
    if paper.published:
        month = month_names[paper.published.month - 1]
        month_line = f"\n        month = {month},"

    year_line = f"\n         year = {year}," if year is not None else ""

    bibtex = f"""@ARTICLE{{{cite_key},
       author = {{{authors}}},
        title = "{{{title}}}",{year_line}{month_line}
       eprint = {{{paper.arxiv_id}}},
archivePrefix = {{arXiv}},
 primaryClass = {{{primary_class}}},
       adsurl = {{https://ui.adsabs.harvard.edu/abs/arXiv:{paper.arxiv_id}}}
}}"""

    return bibtex


def parse_bibtex_for_publication_status(bibtex: str) -> dict:
    """
    Parse BibTeX to determine if it represents a published paper.

    Returns dict with:
        - published: bool
        - journal: str or None
        - doi: str or None
        - volume: str or None
    """
    result = {
        "published": False,
        "journal": None,
        "doi": None,
        "volume": None,
    }

    # Check for journal field
    journal_match = re.search(r'journal\s*=\s*[{"]?([^},"\n]+)', bibtex, re.IGNORECASE)
    if journal_match:
        journal = journal_match.group(1).strip()
        # Ignore if it's just arXiv
        if "arxiv" not in journal.lower():
            result["journal"] = journal
            result["published"] = True

    # Check for DOI
    doi_match = re.search(r'doi\s*=\s*[{"]?([^},"\n]+)', bibtex, re.IGNORECASE)
    if doi_match:
        result["doi"] = doi_match.group(1).strip()
        result["published"] = True

    # Check for volume (another indicator of publication)
    volume_match = re.search(r'volume\s*=\s*[{"]?([^},"\n]+)', bibtex, re.IGNORECASE)
    if volume_match:
        result["volume"] = volume_match.group(1).strip()

    return result


def update_cite_key_in_bibtex(bibtex: str, new_key: str) -> str:
    """Replace the cite key in a BibTeX entry."""
    # Match @TYPE{oldkey, and replace with @TYPE{newkey,
    return re.sub(r"(@\w+\s*\{)\s*[^,]+,", rf"\1{new_key},", bibtex, count=1)
