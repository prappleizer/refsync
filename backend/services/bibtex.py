"""
BibTeX generation and management service.
"""

import re
from typing import Optional

from ..models import Paper


def generate_cite_key(paper: Paper, existing_keys: Optional[set[str]] = None) -> str:
    """
    Generate a cite key in format LastName:Year (e.g., McCallum:2025).
    Handles duplicates with a, b, c suffixes.

    Args:
        paper: Paper to generate key for
        existing_keys: Set of existing cite keys to avoid collisions

    Returns:
        Unique cite key string
    """
    existing_keys = existing_keys or set()

    # Extract first author's last name
    if paper.authors:
        first_author = paper.authors[0]
        # Handle formats like "John Smith" or "Smith, John"
        if "," in first_author:
            last_name = first_author.split(",")[0].strip()
        else:
            parts = first_author.strip().split()
            # Skip suffixes like Jr., III, etc.
            suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "md"}
            last_name = parts[-1]
            for i in range(len(parts) - 1, -1, -1):
                if parts[i].lower().rstrip(".") not in suffixes:
                    last_name = parts[i]
                    break
    else:
        last_name = "Unknown"

    # Clean the last name (remove special characters, keep accents)
    last_name = re.sub(r"[^\w\s-]", "", last_name).strip()

    # Get year from published date
    year = paper.published.year

    # Base key
    base_key = f"{last_name}:{year}"

    # Check for collisions and add suffix if needed
    if base_key not in existing_keys:
        return base_key

    # Try a, b, c, ... suffixes
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = f"{base_key}{suffix}"
        if candidate not in existing_keys:
            return candidate

    # Fallback: add arxiv ID
    return f"{base_key}_{paper.arxiv_id.replace('.', '_')}"


def format_authors_bibtex(authors: list[str]) -> str:
    """
    Format author list for BibTeX.
    Converts "First Last" to "{Last}, First" format and joins with " and ".
    """
    formatted = []
    for author in authors:
        author = author.strip()
        if "," in author:
            # Already in "Last, First" format
            formatted.append(f"{{{author}}}")
        else:
            parts = author.split()
            if len(parts) >= 2:
                # Assume last word is last name (simplified)
                last = parts[-1]
                first = " ".join(parts[:-1])
                formatted.append(f"{{{last}}}, {first}")
            else:
                formatted.append(f"{{{author}}}")

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

    Args:
        paper: Paper object with metadata
        cite_key: Citation key to use

    Returns:
        BibTeX string
    """
    authors = format_authors_bibtex(paper.authors)
    title = escape_bibtex(paper.title)
    year = paper.published.year

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
    month = month_names[paper.published.month - 1]

    bibtex = f"""@ARTICLE{{{cite_key},
       author = {{{authors}}},
        title = "{{{title}}}",
         year = {year},
        month = {month},
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
