"""
Read-only view of the refsync library, for "already in refsync" badges.

Matches on refsync id, arXiv id, bibcode or DOI, so a paper is recognized
however it was added to refsync. The index is reloaded whenever the library
file changes.
"""

import sqlite3
from pathlib import Path
from typing import Optional


class Catalog:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._stamp = None
        self._by_id: dict[str, str] = {}
        self._by_arxiv: dict[str, str] = {}
        self._by_bibcode: dict[str, str] = {}
        self._by_doi: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return self.db_path.exists()

    def _current_stamp(self):
        stamps = []
        for suffix in ("", "-wal"):
            p = Path(str(self.db_path) + suffix)
            if p.exists():
                st = p.stat()
                stamps.append((st.st_mtime_ns, st.st_size))
        return tuple(stamps)

    def invalidate(self) -> None:
        self._stamp = None

    def _refresh(self) -> None:
        stamp = self._current_stamp()
        if stamp == self._stamp:
            return
        by_id, by_arxiv, by_bib, by_doi = {}, {}, {}, {}
        if self.available:
            try:
                conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)
                try:
                    rows = conn.execute("SELECT id, arxiv_id, bibcode, doi FROM papers").fetchall()
                finally:
                    conn.close()
            except sqlite3.Error as e:
                print(f"Could not read refsync library at {self.db_path}: {e}")
                rows = []
            for rid, arxiv_id, bibcode, doi in rows:
                by_id[rid] = rid
                if arxiv_id:
                    by_arxiv[arxiv_id] = rid
                if bibcode:
                    by_bib[bibcode] = rid
                if doi:
                    by_doi[doi.lower()] = rid
        self._by_id, self._by_arxiv, self._by_bibcode, self._by_doi = (
            by_id,
            by_arxiv,
            by_bib,
            by_doi,
        )
        self._stamp = stamp

    def match(self, paper) -> Optional[str]:
        """refsync id for an explore paper (row or dict with id/arxiv_id/bibcode/doi)."""
        self._refresh()
        return (
            self._by_id.get(paper["id"])
            or (paper["arxiv_id"] and self._by_arxiv.get(paper["arxiv_id"]))
            or (paper["bibcode"] and self._by_bibcode.get(paper["bibcode"]))
            or (paper["doi"] and self._by_doi.get(paper["doi"].lower()))
            or None
        )

    def annotate(self, items: list[dict]) -> list[dict]:
        """Set item['refsync_id'] on dicts that have id/arxiv_id/bibcode/doi."""
        # The live library wins: a paper deleted in refsync loses its badge. The
        # id recorded at promote time is only a fallback when refsync's library
        # isn't readable here.
        live = self.available
        for it in items:
            it["refsync_id"] = self.match(it) if live else it.get("refsync_id")
        return items
