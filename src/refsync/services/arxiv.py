import re
from datetime import datetime
from typing import Optional

import feedparser
import httpx

from ..models import Paper
from .bibtex import generate_arxiv_bibtex, generate_cite_key
from .latex import latex_to_text

# Patterns to extract arXiv ID from various URL formats
ARXIV_PATTERNS = [
    r"arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)",  # arxiv.org/abs/2301.07041
    r"arxiv\.org/pdf/(\d{4}\.\d{4,5}(?:v\d+)?)",  # arxiv.org/pdf/2301.07041
    r"arxiv\.org/abs/([a-z-]+/\d{7})",  # arxiv.org/abs/astro-ph/0601234 (old format)
    r"^(\d{4}\.\d{4,5}(?:v\d+)?)$",  # Just the ID: 2301.07041
    r"^([a-z-]+/\d{7})$",  # Just the ID: astro-ph/0601234
]


def parse_arxiv_id(url_or_id: str) -> Optional[str]:
    """Extract arXiv ID from URL or raw ID string"""
    url_or_id = url_or_id.strip()

    for pattern in ARXIV_PATTERNS:
        match = re.search(pattern, url_or_id, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def normalize_arxiv_id(arxiv_id: str) -> str:
    """Remove version suffix if present (2301.07041v2 -> 2301.07041)"""
    return re.sub(r"v\d+$", "", arxiv_id)


class ArxivAPIError(Exception):
    """Error fetching from arXiv API"""

    pass


async def fetch_arxiv_paper(url_or_id: str) -> Paper:
    """
    Fetch paper metadata from arXiv API.

    Args:
        url_or_id: arXiv URL or ID (e.g., "2301.07041" or "https://arxiv.org/abs/2301.07041")

    Returns:
        Paper object with metadata from arXiv

    Raises:
        ArxivAPIError: If the paper cannot be fetched
    """
    arxiv_id = parse_arxiv_id(url_or_id)
    if not arxiv_id:
        raise ArxivAPIError(f"Could not parse arXiv ID from: {url_or_id}")

    # Normalize ID (remove version)
    base_id = normalize_arxiv_id(arxiv_id)

    # Query arXiv API (HTTPS required)
    api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(
                api_url,
                timeout=30.0,
                headers={
                    "User-Agent": "arXiv-Library/1.0 (Academic paper management tool)"
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise ArxivAPIError(f"Failed to fetch from arXiv API: {e}")

    # Parse Atom feed
    feed = feedparser.parse(response.text)

    if not feed.entries:
        raise ArxivAPIError(f"No paper found with ID: {arxiv_id}")

    entry = feed.entries[0]

    # Check for error response
    if "arxiv_id" not in entry.get("id", "").lower() and entry.get("title") == "Error":
        raise ArxivAPIError(f"arXiv API error: {entry.get('summary', 'Unknown error')}")

    # Extract and clean data
    title = latex_to_text(entry.get("title", "").replace("\n", " ").strip())

    authors = [author.get("name", "") for author in entry.get("authors", [])]

    abstract = latex_to_text(entry.get("summary", "").strip())

    # Get categories
    categories = [
        tag["term"]
        for tag in entry.get("tags", [])
        if tag.get("scheme") == "http://arxiv.org/schemas/atom"
    ]
    if not categories:
        categories = [entry.get("arxiv_primary_category", {}).get("term", "unknown")]

    # Parse dates
    published = datetime.strptime(entry.get("published", ""), "%Y-%m-%dT%H:%M:%SZ")
    updated = datetime.strptime(
        entry.get("updated", entry.get("published", "")), "%Y-%m-%dT%H:%M:%SZ"
    )

    # Build URLs
    # Extract clean ID from the entry
    entry_id = entry.get("id", "")
    if "/abs/" in entry_id:
        clean_id = entry_id.split("/abs/")[-1]
    else:
        clean_id = base_id

    arxiv_url = f"https://arxiv.org/abs/{clean_id}"
    pdf_url = f"https://arxiv.org/pdf/{clean_id}.pdf"

    # Check for DOI/journal_ref in arXiv metadata (if author updated it)
    doi = entry.get("arxiv_doi")
    journal_ref = entry.get("arxiv_journal_ref")

    # Create paper first (without bibtex - we'll add it after)
    paper = Paper(
        arxiv_id=base_id,
        title=title,
        authors=authors,
        abstract=abstract,
        categories=categories,
        published=published,
        updated=updated,
        pdf_url=pdf_url,
        arxiv_url=arxiv_url,
        added_at=datetime.utcnow(),
        doi=doi,
        journal_ref=journal_ref,
    )

    paper.cite_key = generate_cite_key(paper)
    paper.bibtex = generate_arxiv_bibtex(paper, paper.cite_key)
    paper.bibtex_source = "arxiv"

    return paper
