"""
Settings for refsync-explore.

Explore keeps its own database and PDF cache under refsync's data directory
(~/.refsync/explore by default), and shares refsync's ADS key and library.
"""

import os
from pathlib import Path
from typing import Optional

from refsync.config import settings as refsync_settings


class ExploreSettings:
    def __init__(
        self,
        data_dir: Optional[Path] = None,
        refsync_db_path: Optional[Path] = None,
        refsync_pdf_dir: Optional[Path] = None,
        refsync_port: Optional[int] = None,
    ):
        env_dir = os.environ.get("REFSYNC_EXPLORE_DIR")
        self.data_dir = Path(data_dir or env_dir or refsync_settings.data_dir / "explore")
        self.db_path = self.data_dir / "explore.db"
        self.pdf_dir = self.data_dir / "pdfs"

        # refsync's library (read for "in refsync" badges, written on promote)
        self.refsync_db_path = Path(refsync_db_path or refsync_settings.database_path)
        self.refsync_pdf_dir = Path(refsync_pdf_dir or refsync_settings.pdf_dir)
        # Port the refsync server runs on, for links from explore's UI
        self.refsync_port = int(refsync_port or os.environ.get("REFSYNC_PORT", "8000"))

        self.package_dir = Path(__file__).parent
        self.templates_dir = self.package_dir / "frontend" / "templates"
        self.static_dir = self.package_dir / "frontend" / "static"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
