from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings
from .db import SQLiteDatabase, SQLitePaperRepository, SQLiteShelfRepository, SQLiteTagRepository
from .routers import papers, shelves, tags
from .routers import settings as settings_router

# Database instance
db = SQLiteDatabase(settings.database_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await db.connect()

    # Initialize repositories
    paper_repo = SQLitePaperRepository(db)
    shelf_repo = SQLiteShelfRepository(db)
    tag_repo = SQLiteTagRepository(db)

    # Inject into routers
    papers.set_paper_repo(paper_repo)
    shelves.set_repos(shelf_repo, paper_repo)
    tags.set_tag_repo(tag_repo)

    yield

    # Shutdown
    await db.disconnect()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Mount static files (convert Path to str for FastAPI)
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
app.mount("/uploads", StaticFiles(directory=str(settings.uploads_dir)), name="uploads")

# Templates
templates = Jinja2Templates(directory=str(settings.templates_dir))

# Include API routers
app.include_router(papers.router)
app.include_router(shelves.router)
app.include_router(tags.router)
app.include_router(settings_router.router)


# Page routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page - add papers"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/library", response_class=HTMLResponse)
async def library(request: Request):
    """Library view - browse papers and shelves"""
    return templates.TemplateResponse("library.html", {"request": request})


@app.get("/paper/{arxiv_id:path}", response_class=HTMLResponse)
async def paper_detail(request: Request, arxiv_id: str):
    """Single paper detail view"""
    return templates.TemplateResponse("paper.html", {"request": request, "arxiv_id": arxiv_id})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings page"""
    return templates.TemplateResponse("settings.html", {"request": request})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
