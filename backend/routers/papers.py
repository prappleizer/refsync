import os
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from typing import Optional

from backend.models import Paper, PaperCreate, PaperUpdate, SearchQuery, SearchResult
from backend.services import fetch_arxiv_paper, ArxivAPIError
from backend.db import PaperRepository
from backend.config import settings


router = APIRouter(prefix="/api/papers", tags=["papers"])


# Dependency injection - will be set by main.py
_paper_repo: Optional[PaperRepository] = None


def get_paper_repo() -> PaperRepository:
    if _paper_repo is None:
        raise RuntimeError("Paper repository not initialized")
    return _paper_repo


def set_paper_repo(repo: PaperRepository):
    global _paper_repo
    _paper_repo = repo


@router.post("", response_model=Paper)
async def add_paper(
    data: PaperCreate,
    repo: PaperRepository = Depends(get_paper_repo)
):
    """
    Add a paper to the library from an arXiv URL or ID.
    """
    try:
        paper = await fetch_arxiv_paper(data.arxiv_url)
    except ArxivAPIError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Check if already exists
    if await repo.exists(paper.arxiv_id):
        existing = await repo.get(paper.arxiv_id)
        raise HTTPException(
            status_code=409, 
            detail=f"Paper already in library",
        )
    
    return await repo.create(paper)


@router.get("", response_model=list[Paper])
async def list_papers(
    limit: int = 50,
    offset: int = 0,
    repo: PaperRepository = Depends(get_paper_repo)
):
    """List all papers in the library."""
    return await repo.list_all(limit=limit, offset=offset)


@router.get("/search", response_model=SearchResult)
async def search_papers(
    q: Optional[str] = None,
    tags: Optional[str] = None,  # Comma-separated
    shelves: Optional[str] = None,  # Comma-separated
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    repo: PaperRepository = Depends(get_paper_repo)
):
    """Search papers with filters."""
    from backend.models import ReadingStatus
    
    query = SearchQuery(
        q=q,
        tags=tags.split(",") if tags else None,
        shelves=shelves.split(",") if shelves else None,
        status=ReadingStatus(status) if status else None,
        limit=limit,
        offset=offset
    )
    return await repo.search(query)


@router.get("/{arxiv_id}", response_model=Paper)
async def get_paper(
    arxiv_id: str,
    repo: PaperRepository = Depends(get_paper_repo)
):
    """Get a specific paper by arXiv ID."""
    paper = await repo.get(arxiv_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


@router.patch("/{arxiv_id}", response_model=Paper)
async def update_paper(
    arxiv_id: str,
    data: PaperUpdate,
    repo: PaperRepository = Depends(get_paper_repo)
):
    """Update paper metadata (shelves, tags, status, notes)."""
    paper = await repo.update(arxiv_id, data)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


@router.delete("/{arxiv_id}")
async def delete_paper(
    arxiv_id: str,
    repo: PaperRepository = Depends(get_paper_repo)
):
    """Remove a paper from the library."""
    # Delete cover image if exists
    paper = await repo.get(arxiv_id)
    if paper and paper.cover_image:
        cover_path = settings.uploads_dir / paper.cover_image
        if cover_path.exists():
            cover_path.unlink()
    
    if not await repo.delete(arxiv_id):
        raise HTTPException(status_code=404, detail="Paper not found")
    
    return {"status": "deleted"}


@router.post("/{arxiv_id}/cover", response_model=Paper)
async def upload_cover(
    arxiv_id: str,
    file: UploadFile = File(...),
    repo: PaperRepository = Depends(get_paper_repo)
):
    """Upload a cover image for a paper."""
    paper = await repo.get(arxiv_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    
    # Validate file type
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}"
        )
    
    # Generate filename
    ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
    filename = f"{arxiv_id.replace('/', '_')}_{uuid.uuid4().hex[:8]}.{ext}"
    
    # Delete old cover if exists
    if paper.cover_image:
        old_path = settings.uploads_dir / paper.cover_image
        if old_path.exists():
            old_path.unlink()
    
    # Save new cover
    file_path = settings.uploads_dir / filename
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
    
    return await repo.set_cover(arxiv_id, filename)


@router.delete("/{arxiv_id}/cover", response_model=Paper)
async def delete_cover(
    arxiv_id: str,
    repo: PaperRepository = Depends(get_paper_repo)
):
    """Remove a paper's cover image."""
    paper = await repo.get(arxiv_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    
    if paper.cover_image:
        cover_path = settings.uploads_dir / paper.cover_image
        if cover_path.exists():
            cover_path.unlink()
    
    return await repo.set_cover(arxiv_id, None)
