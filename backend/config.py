from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    app_name: str = "arXiv Library"
    debug: bool = True
    
    # Paths
    base_dir: Path = Path(__file__).parent.parent
    database_path: Path = base_dir / "library.db"
    uploads_dir: Path = base_dir / "uploads"
    templates_dir: Path = base_dir / "frontend" / "templates"
    static_dir: Path = base_dir / "frontend" / "static"
    
    # Database
    db_type: str = "sqlite"  # sqlite, mongodb, postgres
    
    # arXiv API
    arxiv_api_base: str = "http://export.arxiv.org/api/query"
    
    class Config:
        env_file = ".env"


settings = Settings()

# Ensure directories exist
settings.uploads_dir.mkdir(parents=True, exist_ok=True)
