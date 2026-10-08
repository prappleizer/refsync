"""refsync-explore FastAPI app."""

from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from refsync.annotations import AnnotationStore
from refsync.annotations.router import build_router as build_annotations_router
from refsync.routers import settings as refsync_settings_router

from . import __version__, store
from .config import ExploreSettings
from .db import ExploreDB
from .routers import papers, projects, search
from .services.catalog import Catalog
from .services.pdf_cache import PdfCache


def create_app(cfg: Optional[ExploreSettings] = None, pdf_transport=None) -> FastAPI:
    cfg = cfg or ExploreSettings()
    cfg.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = ExploreDB(cfg.db_path)
        await db.connect()
        app.state.cfg = cfg
        app.state.db = db
        app.state.catalog = Catalog(cfg.refsync_db_path)
        app.state.pdf_cache = PdfCache(db, cfg.pdf_dir, transport=pdf_transport)
        app.state.rate = None
        annotations = AnnotationStore(
            cfg.annotations_db_path,
            cfg.annotations_dir,
            library_db_path=cfg.refsync_db_path,
            uploads_dir=cfg.refsync_uploads_dir,
        )
        await annotations.connect()
        app.state.annotations = annotations
        app.state.explore_db_path = cfg.db_path
        yield
        await app.state.pdf_cache.close()
        await annotations.disconnect()
        await db.disconnect()

    app = FastAPI(title="refsync-explore", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # Browsers attach Origin to cross-site POSTs; refuse writes that come
        # from some other website's page (e.g. a form posting a fake PDF).
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=str(cfg.static_dir)), name="static")
    app.mount("/shared", StaticFiles(directory=str(cfg.shared_static_dir)), name="shared")
    templates = Jinja2Templates(directory=str(cfg.templates_dir))

    app.include_router(projects.router)
    app.include_router(search.router)
    app.include_router(papers.router)
    # Same ADS key endpoints as refsync (the key itself is shared)
    app.include_router(refsync_settings_router.router)
    app.include_router(build_annotations_router("explore"))

    def page(request: Request, name: str, **context):
        context.setdefault("refsync_port", cfg.refsync_port)
        context.setdefault("version", __version__)
        return templates.TemplateResponse(request, name, context=context)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        return page(request, "home.html")

    @app.get("/p/{pid}", response_class=HTMLResponse)
    async def workspace(request: Request, pid: str):
        project = await store.get_project(request.app.state.db.conn, pid)
        if not project:
            return RedirectResponse("/")
        return page(request, "workspace.html", project=project)

    return app


app = create_app()
