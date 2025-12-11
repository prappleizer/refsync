"""
PDF download service for offline paper storage.
"""

import re
from pathlib import Path
from typing import Optional

import httpx

from ..config import settings
from ..models import Paper


def generate_pdf_filename(paper: Paper) -> str:
    """
    Generate a sensible filename for a PDF.
    Format: Author_Year_arxiv_id.pdf
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
            last_name = parts[-1]
            for i in range(len(parts) - 1, -1, -1):
                if parts[i].lower().rstrip(".") not in suffixes:
                    last_name = parts[i]
                    break
    else:
        last_name = "Unknown"

    # Clean the last name (remove special chars)
    last_name = re.sub(r"[^\w\s-]", "", last_name).strip()

    # Get year from publication date
    year = paper.published.year

    # Clean arxiv_id for filename (replace / with _)
    arxiv_id_clean = paper.arxiv_id.replace("/", "_")

    return f"{last_name}_{year}_{arxiv_id_clean}.pdf"


async def download_pdf(paper: Paper) -> Optional[str]:
    """
    Download a paper's PDF from arXiv and save locally.

    Returns the filename if successful, None if failed.
    """
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
        print(f"Error downloading PDF for {paper.arxiv_id}: {e}")
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


def find_pdf_by_arxiv_id(arxiv_id: str) -> Optional[str]:
    """
    Find a PDF by arxiv_id by scanning the pdf directory.
    Useful for recovery/verification.
    """
    arxiv_id_clean = arxiv_id.replace("/", "_")
    for pdf_file in settings.pdf_dir.glob("*.pdf"):
        if arxiv_id_clean in pdf_file.name:
            return pdf_file.name
    return None
