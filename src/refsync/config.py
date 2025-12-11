import os
from pathlib import Path

from pydantic_settings import BaseSettings


def get_data_dir() -> Path:
    """
    Get the data directory for RefSync.
    Uses ~/.refsync by default, or REFSYNC_DATA_DIR env var.
    """
    if env_dir := os.environ.get("REFSYNC_DATA_DIR"):
        return Path(env_dir)
    return Path.home() / ".refsync"


class Settings(BaseSettings):
    app_name: str = "RefSync"
    debug: bool = False

    # Package directory (where code lives)
    package_dir: Path = Path(__file__).parent

    # Data directory (where user data lives)
    data_dir: Path = get_data_dir()

    # Paths derived from above
    @property
    def database_path(self) -> Path:
        return self.data_dir / "library.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def templates_dir(self) -> Path:
        return self.package_dir / "frontend" / "templates"

    @property
    def static_dir(self) -> Path:
        return self.package_dir / "frontend" / "static"

    # For backwards compatibility
    @property
    def base_dir(self) -> Path:
        return self.data_dir

    # Database
    db_type: str = "sqlite"

    # arXiv API
    arxiv_api_base: str = "https://export.arxiv.org/api/query"

    class Config:
        env_file = ".env"


settings = Settings()

# Ensure data directories exist
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.uploads_dir.mkdir(parents=True, exist_ok=True)
settings.pdf_dir.mkdir(parents=True, exist_ok=True)
