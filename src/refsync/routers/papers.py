import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..config import settings
from ..db import PaperRepository
from ..models import Paper, PaperCreate, PaperUpdate, SearchQuery, SearchResult
from ..services import ArxivAPIError, fetch_arxiv_paper

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
async def add_paper(data: PaperCreate, repo: PaperRepository = Depends(get_paper_repo)):
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
            detail="Paper already in library",
        )

    return await repo.create(paper)


@router.get("", response_model=list[Paper])
async def list_papers(
    limit: int = 50, offset: int = 0, repo: PaperRepository = Depends(get_paper_repo)
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
    repo: PaperRepository = Depends(get_paper_repo),
):
    """Search papers with filters."""
    from ..models import ReadingStatus

    query = SearchQuery(
        q=q,
        tags=tags.split(",") if tags else None,
        shelves=shelves.split(",") if shelves else None,
        status=ReadingStatus(status) if status else None,
        limit=limit,
        offset=offset,
    )
    return await repo.search(query)


@router.get("/{arxiv_id}", response_model=Paper)
async def get_paper(arxiv_id: str, repo: PaperRepository = Depends(get_paper_repo)):
    """Get a specific paper by arXiv ID."""
    paper = await repo.get(arxiv_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


@router.patch("/{arxiv_id}", response_model=Paper)
async def update_paper(
    arxiv_id: str, data: PaperUpdate, repo: PaperRepository = Depends(get_paper_repo)
):
    """Update paper metadata (shelves, tags, status, notes)."""
    paper = await repo.update(arxiv_id, data)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


@router.delete("/{arxiv_id}")
async def delete_paper(arxiv_id: str, repo: PaperRepository = Depends(get_paper_repo)):
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
    repo: PaperRepository = Depends(get_paper_repo),
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
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}",
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
async def delete_cover(arxiv_id: str, repo: PaperRepository = Depends(get_paper_repo)):
    """Remove a paper's cover image."""
    paper = await repo.get(arxiv_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    if paper.cover_image:
        cover_path = settings.uploads_dir / paper.cover_image
        if cover_path.exists():
            cover_path.unlink()

    return await repo.set_cover(arxiv_id, None)


@router.post("/sync-citations")
async def sync_citations(
    repo: PaperRepository = Depends(get_paper_repo), only_unsynced: bool = True
):
    """
    Sync all papers with NASA ADS to get updated citation information.

    Args:
        only_unsynced: If True, only sync papers that haven't been synced before
                       or that aren't marked as published yet.
    """
    from ..services.ads import ADSError, sync_papers_with_ads
    from ..services.settings_service import has_ads_api_key

    if not has_ads_api_key():
        raise HTTPException(
            status_code=400,
            detail="ADS API key not configured. Please add your key in Settings.",
        )

    # Get papers to sync
    all_papers = await repo.list_all(limit=2000)

    if only_unsynced:
        # Filter to papers that haven't been synced or aren't published yet
        papers_to_sync = [
            p for p in all_papers if not p.is_published or not p.last_citation_sync
        ]
    else:
        papers_to_sync = all_papers

    if not papers_to_sync:
        return {
            "status": "success",
            "message": "All papers are already synced",
            "stats": {"synced": 0, "published": 0, "unchanged": 0},
        }

    # Create update callback
    async def update_paper(arxiv_id: str, updates: dict):
        await repo.update(arxiv_id, PaperUpdate(**updates))

    try:
        stats = await sync_papers_with_ads(papers_to_sync, update_paper)

        return {
            "status": "success",
            "message": f"Synced {stats['synced']} papers, {stats['published']} published",
            "stats": stats,
        }
    except ADSError as e:
        raise HTTPException(status_code=400, detail=str(e))
