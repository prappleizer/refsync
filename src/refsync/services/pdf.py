"""
PDF download service for offline paper storage.
"""

import re
from pathlib import Path
from typing import Optional

import httpx

from ..config import settings
from ..models import Paper


def _identifier_slug(paper: Paper) -> str:
    """A filesystem-safe identifier for naming/finding a paper's PDF.

    Prefers the arXiv id, falls back to the bibcode, then the internal id.
    """
    ident = paper.arxiv_id or paper.bibcode or paper.id
    return re.sub(r"[^\w.-]", "_", ident)


def generate_pdf_filename(paper: Paper) -> str:
    """
    Generate a sensible filename for a PDF.
    Format: Author_Year_identifier.pdf
    Example: Pasha_2024_2401.07041.pdf
    """
    # Get first author's last name
    if paper.authors:
        first_author = paper.authors[0]
        # Handle "Last, First" format
        if "," in first_author:
            last_name = first_author.split(",")[0].strip()
        else:
            # Handle "First Last" format
            parts = first_author.strip().split()
            # Skip suffixes like Jr., III
            suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "md"}
            last_name = parts[-1] if parts else "Unknown"
            for i in range(len(parts) - 1, -1, -1):
                if parts[i].lower().rstrip(".") not in suffixes:
                    last_name = parts[i]
                    break
    else:
        last_name = "Unknown"

    # Clean the last name (remove special chars)
    last_name = re.sub(r"[^\w\s-]", "", last_name).strip()

    # Get year from publication date (may be missing for sparse ADS records)
    year = paper.published.year if paper.published else "unknown"

    ident_clean = _identifier_slug(paper)

    return f"{last_name}_{year}_{ident_clean}.pdf"


async def download_pdf(paper: Paper) -> Optional[str]:
    """
    Download a paper's PDF and save locally.

    For arXiv papers this hits the arXiv PDF url; for ADS papers `pdf_url` is the
    ADS link-gateway EPRINT_PDF url, which 302-redirects to the best available
    PDF (usually the arXiv eprint). Returns the filename if successful, else None.
    """
    if not paper.pdf_url:
        return None

    filename = generate_pdf_filename(paper)
    filepath = settings.pdf_dir / filename

    # Check if already downloaded
    if filepath.exists():
        return filename

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            response = await client.get(paper.pdf_url)

            if response.status_code != 200:
                return None

            # Verify it's actually a PDF
            content_type = response.headers.get("content-type", "")
            if "pdf" not in content_type.lower() and not response.content[:4] == b"%PDF":
                return None

            # Save the PDF
            filepath.write_bytes(response.content)
            return filename

    except Exception as e:
        print(f"Error downloading PDF for {paper.id}: {e}")
        return None


def delete_local_pdf(filename: str) -> bool:
    """Delete a locally stored PDF."""
    filepath = settings.pdf_dir / filename
    if filepath.exists():
        filepath.unlink()
        return True
    return False


def get_pdf_path(filename: str) -> Optional[Path]:
    """Get the full path to a local PDF if it exists."""
    filepath = settings.pdf_dir / filename
    if filepath.exists():
        return filepath
    return None


def find_pdf_by_identifier(identifier: str) -> Optional[str]:
    """
    Find a PDF by an identifier (arxiv id or bibcode) by scanning the pdf dir.
    Useful for recovery/verification.
    """
    ident_clean = re.sub(r"[^\w.-]", "_", identifier)
    for pdf_file in settings.pdf_dir.glob("*.pdf"):
        if ident_clean in pdf_file.name:
            return pdf_file.name
    return None
