from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import TagRepository
from ..models import Tag, TagCreate

router = APIRouter(prefix="/api/tags", tags=["tags"])


# Dependency injection
_tag_repo: Optional[TagRepository] = None


def get_tag_repo() -> TagRepository:
    if _tag_repo is None:
        raise RuntimeError("Tag repository not initialized")
    return _tag_repo


def set_tag_repo(repo: TagRepository):
    global _tag_repo
    _tag_repo = repo


class TagColorUpdate(BaseModel):
    color: str


@router.get("", response_model=list[Tag])
async def list_tags(repo: TagRepository = Depends(get_tag_repo)):
    """List all tags."""
    return await repo.list_all()


@router.post("", response_model=Tag)
async def create_tag(data: TagCreate, repo: TagRepository = Depends(get_tag_repo)):
    """Create a new tag."""
    return await repo.create(data)


@router.get("/{name}", response_model=Tag)
async def get_tag(name: str, repo: TagRepository = Depends(get_tag_repo)):
    """Get a specific tag."""
    tag = await repo.get(name)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    return tag


@router.patch("/{name}", response_model=Tag)
async def update_tag_color(
    name: str, data: TagColorUpdate, repo: TagRepository = Depends(get_tag_repo)
):
    """Update a tag's color."""
    tag = await repo.update_color(name, data.color)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    return tag


@router.delete("/{name}")
async def delete_tag(name: str, repo: TagRepository = Depends(get_tag_repo)):
    """Delete a tag."""
    if not await repo.delete(name):
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"status": "deleted"}
