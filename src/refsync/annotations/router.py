"""
HTTP API for annotations, mounted by both refsync and refsync-explore.

Each app puts an AnnotationStore on `app.state.annotations` and the path to
explore's database (for the project list used when tagging notes) on
`app.state.explore_db_path`.
"""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .store import AnnotationError, AnnotationStore, active_projects


class ProjectRef(BaseModel):
    id: str
    name: Optional[str] = None


class AnnotationCreate(BaseModel):
    paper_id: str
    kind: str  # highlight | note | snip
    color: Optional[str] = None
    page: Optional[int] = None
    rects: list[list[float]] = []
    quote: Optional[str] = None
    prefix: Optional[str] = None
    suffix: Optional[str] = None
    note: Optional[str] = None
    image_data_url: Optional[str] = None  # snips: data:image/png;base64,...
    starred: bool = False
    pdf_fingerprint: Optional[str] = None
    projects: list[ProjectRef] = []


class AnnotationUpdate(BaseModel):
    note: Optional[str] = None
    color: Optional[str] = None
    starred: Optional[bool] = None
    projects: Optional[list[ProjectRef]] = None


class AnchorUpdate(BaseModel):
    """Where the annotation sits in another version of the PDF (found by its quote)."""

    fingerprint: str
    state: str  # ok | orphaned
    page: Optional[int] = None
    rects: list[list[float]] = []


def build_router(source: str) -> APIRouter:
    """`source` labels annotations created through this app ("refsync" / "explore")."""
    router = APIRouter(prefix="/api/annotations", tags=["annotations"])

    def _store(request: Request) -> AnnotationStore:
        return request.app.state.annotations

    def _ids(paper_id: str) -> list[str]:
        return [p.strip() for p in (paper_id or "").split(",") if p.strip()]

    @router.get("")
    async def list_annotations(request: Request, paper_id: str, project: Optional[str] = None):
        """Annotations for one paper (or several comma-separated ids of the same paper)."""
        return await _store(request).list(_ids(paper_id), project_id=project)

    @router.get("/summary")
    async def summary(request: Request, paper_id: str = ""):
        """Counts and cover snip per paper, for cards."""
        return await _store(request).summary(_ids(paper_id))

    @router.get("/projects")
    async def projects(request: Request):
        """Active explore projects that notes can be tagged with."""
        return active_projects(getattr(request.app.state, "explore_db_path", None))

    @router.get("/files/{name}")
    async def image(request: Request, name: str):
        path = _store(request).image_path(name)
        if not path:
            raise HTTPException(status_code=404, detail="Image not found")
        return FileResponse(
            path, media_type="image/png", headers={"Cache-Control": "max-age=31536000"}
        )

    @router.post("")
    async def create(request: Request, data: AnnotationCreate):
        payload: dict[str, Any] = data.model_dump()
        payload["projects"] = [p.model_dump() for p in data.projects]
        try:
            return await _store(request).create(payload, source=source)
        except AnnotationError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.patch("/{ann_id}")
    async def update(request: Request, ann_id: str, data: AnnotationUpdate):
        # null means "unchanged", never "clear" (send [] to clear tags)
        fields = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
        if "projects" in fields:
            fields["projects"] = [p.model_dump() for p in data.projects]
        try:
            updated = await _store(request).update(ann_id, fields)
        except AnnotationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not updated:
            raise HTTPException(status_code=404, detail="Annotation not found")
        return updated

    @router.put("/{ann_id}/anchor")
    async def set_anchor(request: Request, ann_id: str, data: AnchorUpdate):
        try:
            updated = await _store(request).set_anchor(
                ann_id, data.fingerprint, data.page, data.rects, data.state
            )
        except AnnotationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not updated:
            raise HTTPException(status_code=404, detail="Annotation not found")
        return updated

    @router.delete("/{ann_id}")
    async def delete(request: Request, ann_id: str):
        if not await _store(request).delete(ann_id):
            raise HTTPException(status_code=404, detail="Annotation not found")
        return {"status": "deleted"}

    return router
