"""Projects, the triage board, tags, and sending papers to refsync."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from refsync.services.ads import ADSError

from .. import store
from ..services import ads_search
from ..services.promote import promote
from .deps import require_project

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    archived: Optional[bool] = None


class AddPapers(BaseModel):
    paper_ids: list[str]
    col: str = "staging"
    search_id: Optional[int] = None


class AddRef(BaseModel):
    ref: str
    col: str = "staging"


class CardUpdate(BaseModel):
    col: Optional[str] = None
    one_liner: Optional[str] = None
    note: Optional[str] = None
    why_not: Optional[str] = None
    read_state: Optional[str] = None
    last_page: Optional[int] = None
    tags: Optional[list[str]] = None


class Reorder(BaseModel):
    col: str
    ids: list[str]


class TagUpdate(BaseModel):
    new_name: Optional[str] = None
    color: Optional[str] = None


class PromoteRequest(BaseModel):
    paper_ids: list[str]
    shelf: Optional[str] = None
    carry_tags: bool = True
    carry_notes: bool = True


def _prefetch_if_kept(request: Request, paper_ids: list[str], col: str) -> None:
    if col in ("staging", "probables") and paper_ids:
        request.app.state.pdf_cache.prefetch(paper_ids)


# --- Projects ---------------------------------------------------------------


@router.get("")
async def list_projects(request: Request, include_archived: bool = False):
    return await store.list_projects(request.app.state.db.conn, include_archived)


@router.post("")
async def create_project(request: Request, data: ProjectCreate):
    return await store.create_project(
        request.app.state.db.conn, data.name.strip(), (data.description or "").strip() or None
    )


@router.get("/{pid}")
async def get_project(request: Request, pid: str):
    return await require_project(request, pid)


@router.patch("/{pid}")
async def update_project(request: Request, pid: str, data: ProjectUpdate):
    await require_project(request, pid)
    return await store.update_project(
        request.app.state.db.conn, pid, data.model_dump(exclude_none=True)
    )


@router.delete("/{pid}")
async def delete_project(request: Request, pid: str):
    if not await store.delete_project(request.app.state.db.conn, pid):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "deleted"}


# --- Board ------------------------------------------------------------------


@router.get("/{pid}/board")
async def get_board(request: Request, pid: str):
    project = await require_project(request, pid)
    st = request.app.state
    cards = st.catalog.annotate(await store.board(st.db.conn, pid))
    return {"project": project, "cards": cards, "tags": await store.list_tags(st.db.conn, pid)}


@router.post("/{pid}/papers")
async def add_papers(request: Request, pid: str, data: AddPapers):
    await require_project(request, pid)
    st = request.app.state
    known = await store.get_papers(st.db.conn, data.paper_ids)
    missing = [i for i in data.paper_ids if i not in known]
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown papers: {', '.join(missing[:5])}")
    try:
        changed = await store.add_to_project(
            st.db.conn, pid, data.paper_ids, data.col, data.search_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _prefetch_if_kept(request, changed, data.col)
    cards = [await store.get_card(st.db.conn, pid, i) for i in data.paper_ids]
    return {"changed": changed, "cards": st.catalog.annotate([c for c in cards if c])}


@router.post("/{pid}/papers/add-ref")
async def add_by_reference(request: Request, pid: str, data: AddRef):
    """Add one paper from a pasted arXiv/ADS link, arXiv id, bibcode or DOI."""
    await require_project(request, pid)
    st = request.app.state
    q = ads_search.ref_to_query(data.ref)
    if not q:
        raise HTTPException(
            status_code=400,
            detail="Couldn't recognize an arXiv/ADS link, arXiv id, bibcode or DOI.",
        )
    try:
        rows, _, rate = await ads_search.run_query(q, rows=1)
    except ADSError as e:
        raise HTTPException(status_code=400, detail=str(e))
    st.rate = rate
    if not rows:
        raise HTTPException(status_code=404, detail="ADS has no record for that reference.")
    await store.upsert_papers(st.db.conn, rows)
    paper_id = rows[0]["id"]
    await store.add_to_project(st.db.conn, pid, [paper_id], data.col)
    _prefetch_if_kept(request, [paper_id], data.col)
    card = await store.get_card(st.db.conn, pid, paper_id)
    return st.catalog.annotate([card])[0]


@router.get("/{pid}/papers/{paper_id}")
async def get_paper_in_project(request: Request, pid: str, paper_id: str):
    """Full paper details plus this project's card (None if it isn't in the project)."""
    await require_project(request, pid)
    st = request.app.state
    row = await store.get_paper(st.db.conn, paper_id)
    if not row:
        raise HTTPException(status_code=404, detail="Paper not found")
    detail = st.catalog.annotate([store.paper_detail(row)])[0]
    detail["pdf_cached"] = st.pdf_cache.cached(paper_id) is not None
    card = await store.get_card(st.db.conn, pid, paper_id)
    return {"paper": detail, "card": st.catalog.annotate([card])[0] if card else None}


@router.patch("/{pid}/papers/{paper_id}")
async def update_card(request: Request, pid: str, paper_id: str, data: CardUpdate):
    await require_project(request, pid)
    st = request.app.state
    fields = data.model_dump(exclude_none=True)
    tags = fields.pop("tags", None)
    try:
        ok = await store.update_card(st.db.conn, pid, paper_id, fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Paper is not in this project")
    if tags is not None:
        await store.set_card_tags(st.db.conn, pid, paper_id, tags)
    if "col" in fields:
        _prefetch_if_kept(request, [paper_id], fields["col"])
    card = await store.get_card(st.db.conn, pid, paper_id)
    return st.catalog.annotate([card])[0]


@router.delete("/{pid}/papers/{paper_id}")
async def remove_paper(request: Request, pid: str, paper_id: str):
    await require_project(request, pid)
    if not await store.remove_from_project(request.app.state.db.conn, pid, paper_id):
        raise HTTPException(status_code=404, detail="Paper is not in this project")
    return {"status": "removed"}


@router.post("/{pid}/reorder")
async def reorder(request: Request, pid: str, data: Reorder):
    await require_project(request, pid)
    try:
        await store.reorder(request.app.state.db.conn, pid, data.col, data.ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _prefetch_if_kept(request, data.ids, data.col)
    return {"status": "ok"}


# --- Tags -------------------------------------------------------------------


@router.get("/{pid}/tags")
async def list_tags(request: Request, pid: str):
    await require_project(request, pid)
    return await store.list_tags(request.app.state.db.conn, pid)


@router.patch("/{pid}/tags/{name}")
async def update_tag(request: Request, pid: str, name: str, data: TagUpdate):
    await require_project(request, pid)
    db = request.app.state.db.conn
    new_name = (data.new_name or "").strip() or None
    if (
        new_name
        and new_name != name
        and new_name in {t["name"] for t in await store.list_tags(db, pid)}
    ):
        raise HTTPException(status_code=409, detail="A tag with that name already exists")
    await store.update_tag(db, pid, name, new_name, data.color)
    return await store.list_tags(db, pid)


@router.delete("/{pid}/tags/{name}")
async def delete_tag(request: Request, pid: str, name: str):
    await require_project(request, pid)
    if not await store.delete_tag(request.app.state.db.conn, pid, name):
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"status": "deleted"}


# --- Send to refsync ----------------------------------------------------------


@router.post("/{pid}/promote")
async def promote_papers(request: Request, pid: str, data: PromoteRequest):
    await require_project(request, pid)
    st = request.app.state
    results = await promote(
        st.db,
        st.catalog,
        st.cfg,
        pid,
        data.paper_ids,
        shelf=data.shelf,
        carry_tags=data.carry_tags,
        carry_notes=data.carry_notes,
        pdf_cache=st.pdf_cache,
    )
    return {"results": results}
