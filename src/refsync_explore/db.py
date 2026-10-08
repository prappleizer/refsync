"""
Explore's SQLite database: connection setup and schema migrations.

Migrations are numbered SQL scripts tracked with PRAGMA user_version, so the
schema can grow (figures, highlights, full-text index) without manual steps.
"""

from pathlib import Path
from typing import Optional

import aiosqlite

MIGRATIONS: list[str] = [
    # --- v1: papers, projects, triage board, tags, search history -------------
    """
    CREATE TABLE papers (
        id TEXT PRIMARY KEY,               -- refsync-compatible id (arxiv-... / ads-...)
        arxiv_id TEXT,
        bibcode TEXT,
        doi TEXT,
        title TEXT NOT NULL,
        authors TEXT NOT NULL DEFAULT '[]',  -- JSON, ADS "Last, First" order
        author_count INTEGER,
        abstract TEXT,
        year TEXT,
        pubdate TEXT,
        pub TEXT,
        bibstem TEXT,
        volume TEXT,
        page TEXT,
        doctype TEXT,
        citation_count INTEGER,
        arxiv_class TEXT NOT NULL DEFAULT '[]',
        esources TEXT NOT NULL DEFAULT '[]',
        pdf_path TEXT,
        pdf_status TEXT NOT NULL DEFAULT 'none',   -- none | ok | failed
        pdf_source TEXT,
        pdf_error TEXT,
        fetched_at TEXT NOT NULL
    );
    CREATE INDEX papers_arxiv ON papers(arxiv_id);
    CREATE INDEX papers_bibcode ON papers(bibcode);

    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        archived INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE searches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        query TEXT NOT NULL,
        sort TEXT NOT NULL DEFAULT '',
        label TEXT,
        num_found INTEGER,
        run_count INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        last_run_at TEXT NOT NULL,
        UNIQUE (project_id, query, sort)
    );

    CREATE TABLE search_hits (
        search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
        paper_id TEXT NOT NULL REFERENCES papers(id),
        rank INTEGER NOT NULL,
        PRIMARY KEY (search_id, paper_id)
    );

    CREATE TABLE project_papers (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        paper_id TEXT NOT NULL REFERENCES papers(id),
        col TEXT NOT NULL DEFAULT 'staging'
            CHECK (col IN ('staging', 'probables', 'rejected')),
        position REAL NOT NULL DEFAULT 0,
        one_liner TEXT,
        note TEXT,
        why_not TEXT,
        read_state TEXT NOT NULL DEFAULT 'unseen'
            CHECK (read_state IN ('unseen', 'skimmed', 'read')),
        last_page INTEGER,
        search_id INTEGER REFERENCES searches(id) ON DELETE SET NULL,
        added_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        promoted_at TEXT,
        refsync_id TEXT,
        PRIMARY KEY (project_id, paper_id)
    );

    CREATE TABLE project_tags (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        color TEXT,
        PRIMARY KEY (project_id, name)
    );

    CREATE TABLE project_paper_tags (
        project_id TEXT NOT NULL,
        paper_id TEXT NOT NULL,
        tag TEXT NOT NULL,
        PRIMARY KEY (project_id, paper_id, tag),
        FOREIGN KEY (project_id, paper_id)
            REFERENCES project_papers(project_id, paper_id) ON DELETE CASCADE,
        FOREIGN KEY (project_id, tag)
            REFERENCES project_tags(project_id, name) ON DELETE CASCADE ON UPDATE CASCADE
    );
    """,
    # --- v2: cached reference / citation lists (for recommendations) ------------
    """
    CREATE TABLE paper_links (
        bibcode TEXT PRIMARY KEY,
        refs TEXT NOT NULL DEFAULT '[]',   -- bibcodes this paper cites
        cites TEXT NOT NULL DEFAULT '[]',  -- bibcodes of papers citing it
        fetched_at TEXT NOT NULL
    );
    """,
]


class ExploreDB:
    """Single shared aiosqlite connection, like refsync's SQLiteDatabase."""

    def __init__(self, path: Path):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self.migrate()

    async def disconnect(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Explore database not connected")
        return self._conn

    async def migrate(self) -> None:
        async with self.conn.execute("PRAGMA user_version") as cur:
            version = (await cur.fetchone())[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            await self.conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {i};\nCOMMIT;")
