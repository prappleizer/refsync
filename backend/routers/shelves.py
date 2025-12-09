from fastapi import APIRouter, HTTPException, Depends
from typing import Optional

from backend.models import Shelf, ShelfCreate, ShelfUpdate, Paper
from backend.db import ShelfRepository, PaperRepository


router = APIRouter(prefix="/api/shelves", tags=["shelves"])


# Dependency injection - will be set by main.py
_shelf_repo: Optional[ShelfRepository] = None
_paper_repo: Optional[PaperRepository] = None


def get_shelf_repo() -> ShelfRepository:
    if _shelf_repo is None:
        raise RuntimeError("Shelf repository not initialized")
    return _shelf_repo


def get_paper_repo() -> PaperRepository:
    if _paper_repo is None:
        raise RuntimeError("Paper repository not initialized")
    return _paper_repo


def set_repos(shelf_repo: ShelfRepository, paper_repo: PaperRepository):
    global _shelf_repo, _paper_repo
    _shelf_repo = shelf_repo
    _paper_repo = paper_repo


@router.get("", response_model=list[Shelf])
async def list_shelves(
    repo: ShelfRepository = Depends(get_shelf_repo)
):
    """List all shelves."""
    return await repo.list_all()


@router.post("", response_model=Shelf)
async def create_shelf(
    data: ShelfCreate,
    repo: ShelfRepository = Depends(get_shelf_repo)
):
    """Create a new shelf."""
    # Check if name already exists
    existing = await repo.get_by_name(data.name)
    if existing:
        raise HTTPException(status_code=409, detail="Shelf with this name already exists")
    
    return await repo.create(data)


@router.get("/{shelf_id}", response_model=Shelf)
async def get_shelf(
    shelf_id: str,
    repo: ShelfRepository = Depends(get_shelf_repo)
):
    """Get a specific shelf."""
    shelf = await repo.get(shelf_id)
    if not shelf:
        raise HTTPException(status_code=404, detail="Shelf not found")
    return shelf


@router.patch("/{shelf_id}", response_model=Shelf)
async def update_shelf(
    shelf_id: str,
    data: ShelfUpdate,
    repo: ShelfRepository = Depends(get_shelf_repo)
):
    """Update a shelf."""
    # Check name uniqueness if changing name
    if data.name:
        existing = await repo.get_by_name(data.name)
        if existing and existing.id != shelf_id:
            raise HTTPException(status_code=409, detail="Shelf with this name already exists")
    
    shelf = await repo.update(shelf_id, data)
    if not shelf:
        raise HTTPException(status_code=404, detail="Shelf not found")
    return shelf


@router.delete("/{shelf_id}")
async def delete_shelf(
    shelf_id: str,
    repo: ShelfRepository = Depends(get_shelf_repo)
):
    """Delete a shelf."""
    if not await repo.delete(shelf_id):
        raise HTTPException(status_code=404, detail="Shelf not found")
    return {"status": "deleted"}


@router.get("/{shelf_id}/papers", response_model=list[Paper])
async def get_shelf_papers(
    shelf_id: str,
    shelf_repo: ShelfRepository = Depends(get_shelf_repo),
    paper_repo: PaperRepository = Depends(get_paper_repo)
):
    """Get all papers in a shelf."""
    # Verify shelf exists
    shelf = await shelf_repo.get(shelf_id)
    if not shelf:
        raise HTTPException(status_code=404, detail="Shelf not found")
    
    from backend.models import SearchQuery
    result = await paper_repo.search(SearchQuery(shelves=[shelf_id], limit=1000))
    return result.papers
