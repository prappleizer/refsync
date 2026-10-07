"""
BibTeX generation and management service.
"""

import re
import unicodedata
from typing import Optional

from ..models import Paper
from .latex import latex_to_text


def _paper_year(paper: Paper) -> Optional[int]:
    """Best-effort publication year: from `published`, else None."""
    if paper.published:
        return paper.published.year
    return None


# === Author name handling ===

# Name suffixes, compared lowercased with trailing "." / "," removed
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md"}

# Letters that Unicode NFKD can't decompose into ASCII + combining accent
_ASCII_FOLD = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "ł": "l",
        "Ł": "L",
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
        "ı": "i",
    }
)


def _is_suffix(token: str) -> bool:
    return token.lower().rstrip(".,") in _SUFFIXES


def _starts_lowercase(word: str) -> bool:
    """BibTeX's von test: is the word's first letter lowercase?"""
    for ch in word:
        if ch.isalpha():
            return ch.islower()
    return False


def _clean_name(name: str) -> str:
    """Turn LaTeX-escaped names (e.g. 'Kere{\\v{s}}') into plain Unicode."""
    name = name.strip()
    if "\\" in name or "{" in name:
        name = latex_to_text(name)
    name = name.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", name).strip()


# Group authors ("Euclid Collaboration", "HSC Team", "SKA Consortium")
_GROUP_WORD = re.compile(r"\b(collaboration|team|consortium)\b", re.IGNORECASE)


def _group_author(name: str) -> Optional[str]:
    """
    Return the full group name if this author is a collaboration/team, else None.

        "Euclid Collaboration"             -> "Euclid Collaboration"
        "Euclid Collaboration: Y. Mellier" -> "Euclid Collaboration"  (arXiv style)
        "Collaboration, Euclid"            -> "Euclid Collaboration"
        "The LIGO Scientific Collaboration"-> "LIGO Scientific Collaboration"
    """
    head = name.split(":", 1)[0].strip()
    parts = [p.strip() for p in head.split(",") if p.strip()]
    if not parts:
        return None
    if len(parts) >= 2 and _GROUP_WORD.fullmatch(parts[0]):
        head = f"{parts[1]} {parts[0]}"
    elif _GROUP_WORD.search(parts[0]):
        head = parts[0]
    else:
        return None
    head = re.sub(r"^the\s+", "", head, flags=re.IGNORECASE).strip()
    return head or None


def _split_space_form(name: str) -> tuple[str, str, str]:
    """Split 'First von Last [Jr.]' into (first, von+last, suffix)."""
    tokens = name.split()
    suffix_tokens = []
    while len(tokens) > 1 and _is_suffix(tokens[-1]):
        suffix_tokens.insert(0, tokens.pop().rstrip(","))
    tokens = [t.rstrip(",") for t in tokens]
    suffix = " ".join(suffix_tokens)

    if not tokens:
        return "", "", suffix
    if len(tokens) == 1:
        return "", tokens[0], suffix

    # BibTeX von rule: the von part starts at the first lowercase word, and the
    # last word always belongs to Last. Everything from the first particle on is
    # the surname: "Pieter van Dokkum" -> ("Pieter", "van Dokkum").
    for i, tok in enumerate(tokens[:-1]):
        if _starts_lowercase(tok):
            return " ".join(tokens[:i]), " ".join(tokens[i:]), suffix
    return " ".join(tokens[:-1]), tokens[-1], suffix


def _split_name(name: str) -> tuple[str, str, str]:
    """
    Split an author name into (first, last, suffix), keeping lowercase
    particles with the surname. Handles both name orders:

        "Pieter van Dokkum"         -> ("Pieter", "van Dokkum", "")
        "van Dokkum, Pieter G."     -> ("Pieter G.", "van Dokkum", "")
        "Arjen van der Wel"         -> ("Arjen", "van der Wel", "")
        "John Smith Jr."            -> ("John", "Smith", "Jr.")
        "Smith, Jr., John"          -> ("John", "Smith", "Jr.")   (BibTeX order)
        "Smith, John, Jr."          -> ("John", "Smith", "Jr.")
        "Euclid Collaboration"      -> ("", "Euclid Collaboration", "")

    LaTeX escapes are converted to Unicode first. Returns ("", "", "") for an
    empty name.
    """
    name = _clean_name(name or "")
    if not name:
        return "", "", ""

    group = _group_author(name)
    if group:
        return "", group, ""

    if "," not in name:
        return _split_space_form(name)

    parts = [p.strip() for p in name.split(",") if p.strip()]
    if len(parts) == 1:
        return _split_space_form(parts[0])
    if len(parts) == 2:
        if _is_suffix(parts[1]):
            # "John Smith, Jr." -- the comma only sets off the suffix
            first, last, _ = _split_space_form(parts[0])
            return first, last, parts[1]
        return parts[1], parts[0], ""

    # Three or more parts: "Last, Jr, First" (BibTeX) or "Last, First, Jr"
    a, b = parts[1], parts[2]
    if _is_suffix(b) and not _is_suffix(a):
        return a, parts[0], b
    return b, parts[0], a


def split_name(name: str) -> tuple[str, str, str]:
    """Public alias of _split_name: (first, last-with-particles, suffix)."""
    return _split_name(name)


def _to_ascii(text: str) -> str:
    """Drop accents and fold special letters: 'Kereš' -> 'Keres', 'Bjørn' -> 'Bjorn'."""
    text = text.translate(_ASCII_FOLD)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def surname_slug(name: str) -> str:
    """
    ASCII-only, space-free surname for cite keys and filenames.

        "Pieter van Dokkum"            -> "van_Dokkum"
        "Claude-André Faucher-Giguère" -> "Faucher-Giguere"
        "Kereš, Dušan"                 -> "Keres"
    """
    _, last, _ = _split_name(name)
    slug = _to_ascii(last)
    slug = re.sub(r"\s+", "_", slug.strip())
    slug = re.sub(r"[^A-Za-z0-9_-]", "", slug)
    return slug or "Unknown"


def generate_cite_key(paper: Paper, existing_keys: Optional[set[str]] = None) -> str:
    """
    Generate a cite key in format LastName:Year (e.g., McCallum:2025).

    Keys are ASCII-only with particles kept and spaces replaced
    (van_Dokkum:2026, Faucher-Giguere:2023). Handles duplicates with
    a, b, c suffixes.

    Args:
        paper: Paper to generate key for
        existing_keys: Set of existing cite keys to avoid collisions

    Returns:
        Unique cite key string
    """
    existing_keys = existing_keys or set()

    last_name = surname_slug(paper.authors[0]) if paper.authors else "Unknown"

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
    tail = re.sub(r"[^A-Za-z0-9_-]", "_", tail)
    return f"{base_key}_{tail}"


def format_authors_bibtex(authors: list[str]) -> str:
    """
    Format author list for BibTeX: "{von Last}, First" (or "{Last}, Jr., First"),
    joined with " and ". Names keep their accents; only cite keys are ASCII-folded.
    """
    formatted = []
    for author in authors:
        first, last, suffix = _split_name(author)
        if not last:
            continue
        entry = f"{{{last}}}"
        if suffix:
            entry += f", {suffix}"
        if first:
            entry += f", {first}"
        formatted.append(entry)

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
    # (lambda avoids re treating backslashes in the key as escapes)
    return re.sub(r"(@\w+\s*\{)\s*[^,]+,", lambda m: f"{m.group(1)}{new_key},", bibtex, count=1)
