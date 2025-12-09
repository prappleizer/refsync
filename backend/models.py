from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from enum import Enum


class ReadingStatus(str, Enum):
    READ = "read"
    TO_READ = "to-read"
    UNSET = ""


# === Paper Models ===

class PaperBase(BaseModel):
    """Core paper data from arXiv"""
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    published: datetime
    updated: datetime
    pdf_url: str
    arxiv_url: str


class PaperCreate(BaseModel):
    """Request to add a paper - just needs the URL"""
    arxiv_url: str


class PaperUpdate(BaseModel):
    """User-editable paper metadata"""
    shelves: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    status: Optional[ReadingStatus] = None
    notes: Optional[str] = None
    starred: Optional[bool] = None


class Paper(PaperBase):
    """Full paper model with user data"""
    shelves: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: ReadingStatus = ReadingStatus.UNSET
    starred: bool = False
    notes: Optional[str] = None
    cover_image: Optional[str] = None
    added_at: datetime = Field(default_factory=datetime.utcnow)
    
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
