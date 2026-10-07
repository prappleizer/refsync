"""PDF serving/upload and app status."""

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from refsync.services.settings_service import has_ads_api_key

from .. import store
from ..services.pdf_cache import PdfUnavailable

router = APIRouter(prefix="/api", tags=["papers"])


@router.get("/papers/{paper_id}/pdf")
async def get_pdf(request: Request, paper_id: str):
    """Serve the cached PDF, downloading it first on a cache miss."""
    st = request.app.state
    try:
        path = await st.pdf_cache.ensure(paper_id)
    except PdfUnavailable as e:
        raise HTTPException(status_code=404, detail=e.message)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.post("/papers/{paper_id}/pdf")
async def upload_pdf(request: Request, paper_id: str, file: UploadFile = File(...)):
    """Attach a PDF by hand (e.g. a publisher PDF that needs a login to download)."""
    st = request.app.state
    if not await store.get_paper(st.db.conn, paper_id):
        raise HTTPException(status_code=404, detail="Paper not found")
    try:
        await st.pdf_cache.save_upload(paper_id, await file.read())
    except PdfUnavailable as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {"status": "ok"}


@router.get("/status")
async def status(request: Request):
    st = request.app.state
    return {
        "ads_key": has_ads_api_key(),
        "refsync_library": st.catalog.available,
        "refsync_port": st.cfg.refsync_port,
        "rate": getattr(st, "rate", None),
    }
