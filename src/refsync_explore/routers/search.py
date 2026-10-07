"""ADS search inside a project, citation hops, and search history."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from refsync.services.ads import ADSError

from .. import store
from ..services import ads_search
from .deps import require_project

router = APIRouter(prefix="/api/projects", tags=["search"])


class SearchRequest(BaseModel):
    q: str
    sort: str = "relevance"
    start: int = 0
    label: Optional[str] = None


class HopRequest(BaseModel):
    paper_id: str
    kind: str  # references | citations | similar
    sort: Optional[str] = None
    start: int = 0


async def _search(request: Request, pid: str, q: str, sort: str, start: int, label=None):
    st = request.app.state
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Empty query")
    try:
        rows, num_found, rate = await ads_search.run_query(q, sort=sort, start=start)
    except ADSError as e:
        raise HTTPException(status_code=400, detail=str(e))
    st.rate = rate

    await store.upsert_papers(st.db.conn, rows)
    ids = [r["id"] for r in rows]
    search_id = await store.record_search(st.db.conn, pid, q, sort, label, num_found, ids, start)

    papers = await store.get_papers(st.db.conn, ids)
    status = await store.project_status(st.db.conn, pid, ids)
    hits = []
    for i in dict.fromkeys(ids):
        row = papers[i]
        hit = store.paper_summary(row)
        hit["abstract"] = row["abstract"]
        hit["status"] = status.get(i)
        hits.append(hit)
    st.catalog.annotate(hits)
    return {
        "search_id": search_id,
        "query": q,
        "sort": sort,
        "label": label,
        "num_found": num_found,
        "start": start,
        "hits": hits,
        "rate": rate,
    }


@router.post("/{pid}/search")
async def search(request: Request, pid: str, data: SearchRequest):
    await require_project(request, pid)
    return await _search(request, pid, data.q, data.sort, data.start, data.label)


@router.post("/{pid}/hop")
async def hop(request: Request, pid: str, data: HopRequest):
    """References / citations / similar papers for one paper, as a search in this project."""
    await require_project(request, pid)
    row = await store.get_paper(request.app.state.db.conn, data.paper_id)
    if not row or not row["bibcode"]:
        raise HTTPException(status_code=404, detail="Paper not found (or has no bibcode)")
    if data.kind not in ads_search.HOP_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown hop kind: {data.kind}")
    summary = store.paper_summary(row)
    label = (
        f"{ads_search.HOP_KINDS[data.kind]} {summary['author_label']} {row['year'] or ''}".strip()
    )
    sort = data.sort or ("citations" if data.kind != "similar" else "relevance")
    q = ads_search.hop_query(data.kind, row["bibcode"])
    return await _search(request, pid, q, sort, data.start, label)


@router.get("/{pid}/searches")
async def searches(request: Request, pid: str):
    await require_project(request, pid)
    return await store.list_searches(request.app.state.db.conn, pid)
