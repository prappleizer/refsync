from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ReadingStatus(str, Enum):
    READ = "read"
    TO_READ = "to-read"
    UNSET = ""


# === Paper Models ===


class PaperBase(BaseModel):
    """Core paper data (from arXiv or ADS)"""

    id: str  # internal primary key (see services.identifiers.make_paper_id)

    # Source identifiers — at least one is present depending on `source`.
    arxiv_id: Optional[str] = None  # bare arXiv id, e.g. "2301.07041" (None for ADS-only)
    bibcode: Optional[str] = None  # ADS bibcode, e.g. "2025ApJ...980L...3P"

    title: str
    authors: list[str]
    abstract: Optional[str] = None  # ADS records may lack an abstract
    categories: list[str] = Field(default_factory=list)
    published: Optional[datetime] = None
    updated: Optional[datetime] = None

    # Links — arxiv fields are None for ADS-only papers; ads_url is the canonical
    # ADS abstract page (present for ADS-sourced papers).
    pdf_url: Optional[str] = None
    arxiv_url: Optional[str] = None
    ads_url: Optional[str] = None

    source: str = "arxiv"  # "arxiv" | "ads"


class PaperCreate(BaseModel):
    """Request to add a paper - accepts an arXiv URL/ID or an ADS URL/bibcode"""

    # Kept the field name `arxiv_url` for frontend compatibility; it now accepts
    # ADS URLs and bibcodes too. `url` is an accepted alias.
    arxiv_url: str


class PaperUpdate(BaseModel):
    """User-editable paper metadata"""

    shelves: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    status: Optional[ReadingStatus] = None
    notes: Optional[str] = None
    starred: Optional[bool] = None
    # Citation fields (updated by ADS sync)
    bibtex: Optional[str] = None
    bibtex_source: Optional[str] = None
    cite_key: Optional[str] = None
    is_published: Optional[bool] = None
    doi: Optional[str] = None
    journal_ref: Optional[str] = None
    bibcode: Optional[str] = None  # was ads_bibcode; now the single identity/sync bibcode
    ads_url: Optional[str] = None
    last_citation_sync: Optional[str] = None  # ISO format string
    # Local PDF
    local_pdf: Optional[str] = None


class Paper(PaperBase):
    """Full paper model with user data"""

    shelves: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: ReadingStatus = ReadingStatus.UNSET
    starred: bool = False
    notes: Optional[str] = None
    cover_image: Optional[str] = None
    added_at: datetime = Field(default_factory=datetime.utcnow)

    # Citation fields
    bibtex: Optional[str] = None
    bibtex_source: str = "arxiv"  # "arxiv" | "ads"
    cite_key: Optional[str] = None  # e.g., "McCallum:2025"
    is_published: bool = False  # True if journal publication detected
    doi: Optional[str] = None
    journal_ref: Optional[str] = None
    last_citation_sync: Optional[datetime] = None

    # Local PDF storage
    local_pdf: Optional[str] = None  # Filename like "Pasha_2024_2401.07041.pdf"

    class Config:
        from_attributes = True


# === Shelf Models ===


class ShelfBase(BaseModel):
    name: str
    description: Optional[str] = None


class ShelfCreate(ShelfBase):
    pass


class ShelfUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class Shelf(ShelfBase):
    id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    paper_count: int = 0

    class Config:
        from_attributes = True


# === Tag Models ===


class TagBase(BaseModel):
    name: str
    color: Optional[str] = None


class TagCreate(TagBase):
    pass


class Tag(TagBase):
    paper_count: int = 0

    class Config:
        from_attributes = True


# === Search Models ===


class SearchQuery(BaseModel):
    q: Optional[str] = None  # Full-text search
    tags: Optional[list[str]] = None
    shelves: Optional[list[str]] = None
    status: Optional[ReadingStatus] = None
    limit: int = 50
    offset: int = 0


class SearchResult(BaseModel):
    papers: list[Paper]
    total: int
