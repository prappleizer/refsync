"""
ADS search for explore: run queries and normalize records into paper rows.

Paper ids follow refsync's rule (arXiv id first, else bibcode), so the same
paper has the same id in explore and in the refsync library.
"""

import json
from datetime import datetime
from typing import Optional

from refsync.services.ads import ADSClient, arxiv_id_from_identifiers
from refsync.services.ads_parse import bibcode_is_arxiv, parse_ads_bibcode
from refsync.services.arxiv import normalize_arxiv_id, parse_arxiv_id
from refsync.services.identifiers import make_paper_id

from ..text import clean_ads_text

SEARCH_FIELDS = ",".join(
    [
        "bibcode",
        "title",
        "author",
        "author_count",
        "abstract",
        "year",
        "pubdate",
        "pub",
        "bibstem",
        "volume",
        "page",
        "doi",
        "doctype",
        "citation_count",
        "identifier",
        "arxiv_class",
        "esources",
    ]
)

SORTS = {
    "relevance": "score desc",
    "date": "date desc",
    "citations": "citation_count desc",
    "oldest": "date asc",
}

HOP_KINDS = {
    "references": "References of",
    "citations": "Citations to",
    "similar": "Similar to",
}

PAGE_SIZE = 50


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def doc_to_row(doc: dict) -> dict:
    """ADS search doc -> explore `papers` row (without PDF fields)."""
    bibcode = doc.get("bibcode")
    arxiv_id = arxiv_id_from_identifiers(doc.get("identifier"))
    if not arxiv_id and bibcode:
        arxiv_id = bibcode_is_arxiv(bibcode)
    page = _first(doc.get("page"))
    return {
        "id": make_paper_id(arxiv_id=arxiv_id, bibcode=bibcode),
        "arxiv_id": arxiv_id,
        "bibcode": bibcode,
        "doi": _first(doc.get("doi")),
        "title": clean_ads_text(_first(doc.get("title"))) or "Untitled",
        "authors": json.dumps(doc.get("author") or []),
        "author_count": doc.get("author_count") or len(doc.get("author") or []),
        "abstract": clean_ads_text(doc.get("abstract")),
        "year": doc.get("year"),
        "pubdate": doc.get("pubdate"),
        "pub": doc.get("pub"),
        "bibstem": _first(doc.get("bibstem")),
        "volume": doc.get("volume"),
        "page": page,
        "doctype": doc.get("doctype"),
        "citation_count": doc.get("citation_count"),
        "arxiv_class": json.dumps(doc.get("arxiv_class") or []),
        "esources": json.dumps(doc.get("esources") or []),
        "fetched_at": datetime.utcnow().isoformat(),
    }


async def run_query(
    q: str, sort: str = "relevance", start: int = 0, rows: int = PAGE_SIZE, client=None
):
    """Run an ADS query. Returns (rows, num_found, rate_limit). Raises ADSError."""
    client = client or ADSClient()
    result = await client.query(
        q, SEARCH_FIELDS, rows=rows, start=start, sort=SORTS.get(sort, sort or None)
    )
    rows_out = [doc_to_row(d) for d in result.docs if d.get("bibcode")]
    return rows_out, result.num_found, result.rate_limit


def hop_query(kind: str, bibcode: str) -> str:
    """references / citations / similar operator query for one paper."""
    if kind not in HOP_KINDS:
        raise ValueError(f"Unknown hop kind: {kind}")
    return f'{kind}(bibcode:"{bibcode}")'


def ref_to_query(text: str) -> Optional[str]:
    """
    Turn a pasted arXiv/ADS link, arXiv id, bibcode or DOI into an ADS query
    for exactly that paper. Returns None if nothing recognizable.
    """
    text = (text or "").strip()
    if not text:
        return None
    arxiv_id = parse_arxiv_id(text)
    if arxiv_id:
        return f'identifier:"arXiv:{normalize_arxiv_id(arxiv_id)}"'
    bibcode = parse_ads_bibcode(text)
    if bibcode:
        aid = bibcode_is_arxiv(bibcode)
        if aid:
            return f'identifier:"arXiv:{aid}"'
        return f'identifier:"{bibcode}"'
    doi = text
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix) :]
    if doi.startswith("10.") and "/" in doi:
        return f'doi:"{doi}"'
    return None
