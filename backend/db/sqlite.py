import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite

from ..models import (
    Paper,
    PaperUpdate,
    ReadingStatus,
    SearchQuery,
    SearchResult,
    Shelf,
    ShelfCreate,
    ShelfUpdate,
    Tag,
    TagCreate,
)
from .base import PaperRepository, ShelfRepository, TagRepository


class SQLiteDatabase:
    """SQLite database connection manager"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._create_tables()

    async def disconnect(self):
        if self._connection:
            await self._connection.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._connection:
            raise RuntimeError("Database not connected")
        return self._connection

    async def _create_tables(self):
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
                arxiv_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                authors TEXT NOT NULL,  -- JSON array
                abstract TEXT NOT NULL,
                categories TEXT NOT NULL,  -- JSON array
                published TEXT NOT NULL,
                updated TEXT NOT NULL,
                pdf_url TEXT NOT NULL,
                arxiv_url TEXT NOT NULL,
                shelves TEXT DEFAULT '[]',  -- JSON array
                tags TEXT DEFAULT '[]',  -- JSON array
                status TEXT DEFAULT '',
                starred INTEGER DEFAULT 0,
                notes TEXT,
                cover_image TEXT,
                added_at TEXT NOT NULL,
                bibtex TEXT,
                bibtex_source TEXT DEFAULT 'arxiv',
                cite_key TEXT,
                is_published INTEGER DEFAULT 0,
                doi TEXT,
                journal_ref TEXT,
                ads_bibcode TEXT,
                last_citation_sync TEXT
            );
            
            CREATE TABLE IF NOT EXISTS shelves (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS tags (
                name TEXT PRIMARY KEY,
                color TEXT
            );
            
            -- Full-text search virtual table
            CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
                arxiv_id,
                title,
                authors,
                abstract,
                notes,
                content='papers',
                content_rowid='rowid'
            );
            
            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
                INSERT INTO papers_fts(arxiv_id, title, authors, abstract, notes)
                VALUES (new.arxiv_id, new.title, new.authors, new.abstract, new.notes);
            END;
            
            CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
                INSERT INTO papers_fts(papers_fts, arxiv_id, title, authors, abstract, notes)
                VALUES ('delete', old.arxiv_id, old.title, old.authors, old.abstract, old.notes);
            END;
            
            CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
                INSERT INTO papers_fts(papers_fts, arxiv_id, title, authors, abstract, notes)
                VALUES ('delete', old.arxiv_id, old.title, old.authors, old.abstract, old.notes);
                INSERT INTO papers_fts(arxiv_id, title, authors, abstract, notes)
                VALUES (new.arxiv_id, new.title, new.authors, new.abstract, new.notes);
            END;
        """)
        await self.conn.commit()


class SQLitePaperRepository(PaperRepository):
    """SQLite implementation of paper repository"""

    def __init__(self, db: SQLiteDatabase):
        self.db = db

    def _row_to_paper(self, row: aiosqlite.Row) -> Paper:
        return Paper(
            arxiv_id=row["arxiv_id"],
            title=row["title"],
            authors=json.loads(row["authors"]),
            abstract=row["abstract"],
            categories=json.loads(row["categories"]),
            published=datetime.fromisoformat(row["published"]),
            updated=datetime.fromisoformat(row["updated"]),
            pdf_url=row["pdf_url"],
            arxiv_url=row["arxiv_url"],
            shelves=json.loads(row["shelves"]),
            tags=json.loads(row["tags"]),
            status=ReadingStatus(row["status"])
            if row["status"]
            else ReadingStatus.UNSET,
            starred=bool(row["starred"]) if row["starred"] is not None else False,
            notes=row["notes"],
            cover_image=row["cover_image"],
            added_at=datetime.fromisoformat(row["added_at"]),
            bibtex=row["bibtex"] if "bibtex" in row.keys() else None,
            bibtex_source=row["bibtex_source"]
            if "bibtex_source" in row.keys()
            else "arxiv",
            cite_key=row["cite_key"] if "cite_key" in row.keys() else None,
            is_published=bool(row["is_published"])
            if "is_published" in row.keys() and row["is_published"] is not None
            else False,
            doi=row["doi"] if "doi" in row.keys() else None,
            journal_ref=row["journal_ref"] if "journal_ref" in row.keys() else None,
            ads_bibcode=row["ads_bibcode"] if "ads_bibcode" in row.keys() else None,
            last_citation_sync=datetime.fromisoformat(row["last_citation_sync"])
            if "last_citation_sync" in row.keys() and row["last_citation_sync"]
            else None,
        )

    async def create(self, paper: Paper) -> Paper:
        await self.db.conn.execute(
            """
            INSERT INTO papers (
                arxiv_id, title, authors, abstract, categories,
                published, updated, pdf_url, arxiv_url,
                shelves, tags, status, starred, notes, cover_image, added_at,
                bibtex, bibtex_source, cite_key, is_published, doi, journal_ref, ads_bibcode, last_citation_sync
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                paper.arxiv_id,
                paper.title,
                json.dumps(paper.authors),
                paper.abstract,
                json.dumps(paper.categories),
                paper.published.isoformat(),
                paper.updated.isoformat(),
                paper.pdf_url,
                paper.arxiv_url,
                json.dumps(paper.shelves),
                json.dumps(paper.tags),
                paper.status.value,
                int(paper.starred),
                paper.notes,
                paper.cover_image,
                paper.added_at.isoformat(),
                paper.bibtex,
                paper.bibtex_source,
                paper.cite_key,
                int(paper.is_published),
                paper.doi,
                paper.journal_ref,
                paper.ads_bibcode,
                paper.last_citation_sync.isoformat()
                if paper.last_citation_sync
                else None,
            ),
        )
        await self.db.conn.commit()
        return paper

    async def get(self, arxiv_id: str) -> Optional[Paper]:
        async with self.db.conn.execute(
            "SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return self._row_to_paper(row) if row else None

    async def update(self, arxiv_id: str, data: PaperUpdate) -> Optional[Paper]:
        paper = await self.get(arxiv_id)
        if not paper:
            return None

        updates = []
        values = []

        if data.shelves is not None:
            updates.append("shelves = ?")
            values.append(json.dumps(data.shelves))
        if data.tags is not None:
            updates.append("tags = ?")
            values.append(json.dumps(data.tags))
        if data.status is not None:
            updates.append("status = ?")
            values.append(data.status.value)
        if data.starred is not None:
            updates.append("starred = ?")
            values.append(int(data.starred))
        if data.notes is not None:
            updates.append("notes = ?")
            values.append(data.notes)

        if updates:
            values.append(arxiv_id)
            await self.db.conn.execute(
                f"UPDATE papers SET {', '.join(updates)} WHERE arxiv_id = ?", values
            )
            await self.db.conn.commit()

        return await self.get(arxiv_id)

    async def delete(self, arxiv_id: str) -> bool:
        cursor = await self.db.conn.execute(
            "DELETE FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        )
        await self.db.conn.commit()
        return cursor.rowcount > 0

    async def list_all(self, limit: int = 50, offset: int = 0) -> list[Paper]:
        async with self.db.conn.execute(
            "SELECT * FROM papers ORDER BY added_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_paper(row) for row in rows]

    async def search(self, query: SearchQuery) -> SearchResult:
        conditions = []
        params = []

        # Full-text search
        if query.q:
            conditions.append("""
                arxiv_id IN (
                    SELECT arxiv_id FROM papers_fts WHERE papers_fts MATCH ?
                )
            """)
            # Escape special FTS characters and create search term
            search_term = query.q.replace('"', '""')
            params.append(f'"{search_term}"')

        # Tag filter
        if query.tags:
            for tag in query.tags:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

        # Shelf filter
        if query.shelves:
            for shelf in query.shelves:
                conditions.append("shelves LIKE ?")
                params.append(f'%"{shelf}"%')

        # Status filter
        if query.status:
            conditions.append("status = ?")
            params.append(query.status.value)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Get total count
        async with self.db.conn.execute(
            f"SELECT COUNT(*) FROM papers WHERE {where_clause}", params
        ) as cursor:
            total = (await cursor.fetchone())[0]

        # Get results
        async with self.db.conn.execute(
            f"""SELECT * FROM papers WHERE {where_clause} 
                ORDER BY added_at DESC LIMIT ? OFFSET ?""",
            params + [query.limit, query.offset],
        ) as cursor:
            rows = await cursor.fetchall()
            papers = [self._row_to_paper(row) for row in rows]

        return SearchResult(papers=papers, total=total)

    async def exists(self, arxiv_id: str) -> bool:
        async with self.db.conn.execute(
            "SELECT 1 FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def set_cover(self, arxiv_id: str, cover_path: str) -> Optional[Paper]:
        await self.db.conn.execute(
            "UPDATE papers SET cover_image = ? WHERE arxiv_id = ?",
            (cover_path, arxiv_id),
        )
        await self.db.conn.commit()
        return await self.get(arxiv_id)


class SQLiteShelfRepository(ShelfRepository):
    """SQLite implementation of shelf repository"""

    def __init__(self, db: SQLiteDatabase):
        self.db = db

    async def _get_paper_count(self, shelf_id: str) -> int:
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM papers WHERE shelves LIKE ?", (f'%"{shelf_id}"%',)
        ) as cursor:
            return (await cursor.fetchone())[0]

    async def _row_to_shelf(self, row: aiosqlite.Row) -> Shelf:
        return Shelf(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at=datetime.fromisoformat(row["created_at"]),
            paper_count=await self._get_paper_count(row["id"]),
        )

    async def create(self, shelf: ShelfCreate) -> Shelf:
        shelf_id = str(uuid.uuid4())[:8]
        now = datetime.utcnow().isoformat()
        await self.db.conn.execute(
            "INSERT INTO shelves (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (shelf_id, shelf.name, shelf.description, now),
        )
        await self.db.conn.commit()
        return await self.get(shelf_id)

    async def get(self, shelf_id: str) -> Optional[Shelf]:
        async with self.db.conn.execute(
            "SELECT * FROM shelves WHERE id = ?", (shelf_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return await self._row_to_shelf(row) if row else None

    async def get_by_name(self, name: str) -> Optional[Shelf]:
        async with self.db.conn.execute(
            "SELECT * FROM shelves WHERE name = ?", (name,)
        ) as cursor:
            row = await cursor.fetchone()
            return await self._row_to_shelf(row) if row else None

    async def update(self, shelf_id: str, data: ShelfUpdate) -> Optional[Shelf]:
        updates = []
        values = []

        if data.name is not None:
            updates.append("name = ?")
            values.append(data.name)
        if data.description is not None:
            updates.append("description = ?")
            values.append(data.description)

        if updates:
            values.append(shelf_id)
            await self.db.conn.execute(
                f"UPDATE shelves SET {', '.join(updates)} WHERE id = ?", values
            )
            await self.db.conn.commit()

        return await self.get(shelf_id)

    async def delete(self, shelf_id: str) -> bool:
        # First remove shelf from all papers
        async with self.db.conn.execute(
            "SELECT arxiv_id, shelves FROM papers WHERE shelves LIKE ?",
            (f'%"{shelf_id}"%',),
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                shelves = json.loads(row["shelves"])
                shelves = [s for s in shelves if s != shelf_id]
                await self.db.conn.execute(
                    "UPDATE papers SET shelves = ? WHERE arxiv_id = ?",
                    (json.dumps(shelves), row["arxiv_id"]),
                )

        cursor = await self.db.conn.execute(
            "DELETE FROM shelves WHERE id = ?", (shelf_id,)
        )
        await self.db.conn.commit()
        return cursor.rowcount > 0

    async def list_all(self) -> list[Shelf]:
        async with self.db.conn.execute(
            "SELECT * FROM shelves ORDER BY name"
        ) as cursor:
            rows = await cursor.fetchall()
            return [await self._row_to_shelf(row) for row in rows]


class SQLiteTagRepository(TagRepository):
    """SQLite implementation of tag repository"""

    def __init__(self, db: SQLiteDatabase):
        self.db = db

    async def _get_paper_count(self, tag_name: str) -> int:
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM papers WHERE tags LIKE ?", (f'%"{tag_name}"%',)
        ) as cursor:
            return (await cursor.fetchone())[0]

    async def _row_to_tag(self, row: aiosqlite.Row) -> Tag:
        return Tag(
            name=row["name"],
            color=row["color"],
            paper_count=await self._get_paper_count(row["name"]),
        )

    async def create(self, tag: TagCreate) -> Tag:
        await self.db.conn.execute(
            "INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)",
            (tag.name, tag.color),
        )
        await self.db.conn.commit()
        return await self.get(tag.name)

    async def get(self, name: str) -> Optional[Tag]:
        async with self.db.conn.execute(
            "SELECT * FROM tags WHERE name = ?", (name,)
        ) as cursor:
            row = await cursor.fetchone()
            return await self._row_to_tag(row) if row else None

    async def delete(self, name: str) -> bool:
        # First remove tag from all papers
        async with self.db.conn.execute(
            "SELECT arxiv_id, tags FROM papers WHERE tags LIKE ?", (f'%"{name}"%',)
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                tags = json.loads(row["tags"])
                tags = [t for t in tags if t != name]
                await self.db.conn.execute(
                    "UPDATE papers SET tags = ? WHERE arxiv_id = ?",
                    (json.dumps(tags), row["arxiv_id"]),
                )

        cursor = await self.db.conn.execute("DELETE FROM tags WHERE name = ?", (name,))
        await self.db.conn.commit()
        return cursor.rowcount > 0

    async def list_all(self) -> list[Tag]:
        async with self.db.conn.execute("SELECT * FROM tags ORDER BY name") as cursor:
            rows = await cursor.fetchall()
            return [await self._row_to_tag(row) for row in rows]

    async def update_color(self, name: str, color: str) -> Optional[Tag]:
        await self.db.conn.execute(
            "UPDATE tags SET color = ? WHERE name = ?", (color, name)
        )
        await self.db.conn.commit()
        return await self.get(name)
