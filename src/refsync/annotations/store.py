"""Storage for annotations (highlights, notes, snips) shared by refsync and explore."""

import asyncio
import base64
import json
import re
import shutil
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import aiosqlite

KINDS = ("highlight", "note", "snip")
MAX_IMAGE_BYTES = 15 * 1024 * 1024
_IMAGE_NAME = re.compile(r"^[0-9a-f]{32}\.png$")

MIGRATIONS = [
    """
    CREATE TABLE annotations (
        id TEXT PRIMARY KEY,
        paper_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('highlight', 'note', 'snip')),
        color TEXT,
        page INTEGER,                        -- 1-based; NULL for a note on the whole paper
        rects TEXT NOT NULL DEFAULT '[]',    -- [[x0, y0, x1, y1], ...] as fractions of the page
        quote TEXT,                          -- highlighted text (also used to re-anchor)
        prefix TEXT,
        suffix TEXT,
        note TEXT,
        image TEXT,                          -- snip PNG file name
        starred INTEGER NOT NULL DEFAULT 0,  -- the paper's cover (snips only)
        pdf_fingerprint TEXT,                -- PDF the page/rects were measured on (never changes)
        source TEXT,                         -- refsync | explore
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX annotations_paper ON annotations(paper_id);

    CREATE TABLE annotation_projects (
        annotation_id TEXT NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL,
        project_name TEXT,
        PRIMARY KEY (annotation_id, project_id)
    );
    """,
    # v2: where a highlight sits in *other* versions of the PDF. The original
    # page/rects (measured on pdf_fingerprint) are never overwritten; each
    # other PDF gets its own row, found by searching for the quoted text.
    """
    CREATE TABLE annotation_anchors (
        annotation_id TEXT NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
        fingerprint TEXT NOT NULL,
        page INTEGER,
        rects TEXT NOT NULL DEFAULT '[]',
        state TEXT NOT NULL CHECK (state IN ('ok', 'orphaned')),
        updated_at TEXT NOT NULL,
        PRIMARY KEY (annotation_id, fingerprint)
    );
    """,
]


def _statements(script: str) -> list[str]:
    """Split a migration script into statements (comments dropped; they may contain ';')."""
    script = re.sub(r"--[^\n]*", "", script)
    return [s.strip() for s in script.split(";") if s.strip()]


class AnnotationError(ValueError):
    pass


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _clean_rects(rects) -> list[list[float]]:
    out = []
    for r in rects or []:
        if not isinstance(r, (list, tuple)) or len(r) != 4:
            raise AnnotationError("rects must be [x0, y0, x1, y1] lists")
        x0, y0, x1, y1 = (float(v) for v in r)
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if not (-0.01 <= x0 <= 1.01 and -0.01 <= y0 <= 1.01 and x1 <= 1.01 and y1 <= 1.01):
            raise AnnotationError("rects are fractions of the page (0..1)")
        out.append(
            [round(max(0.0, v), 5) for v in (x0, y0)] + [round(min(1.0, v), 5) for v in (x1, y1)]
        )
    return out


def _decode_image(data_url: str) -> bytes:
    m = re.match(r"^data:image/png;base64,(.+)$", data_url or "", re.S)
    if not m:
        raise AnnotationError("Snip image must be a PNG data URL")
    raw = base64.b64decode(m.group(1), validate=False)
    if not raw.startswith(b"\x89PNG") or len(raw) > MAX_IMAGE_BYTES:
        raise AnnotationError("Snip image isn't a usable PNG")
    return raw


class AnnotationStore:
    def __init__(
        self,
        db_path: Path,
        files_dir: Path,
        library_db_path: Optional[Path] = None,
        uploads_dir: Optional[Path] = None,
    ):
        self.db_path = Path(db_path)
        self.files_dir = Path(files_dir)
        # refsync library, so a starred snip becomes the paper's card cover there
        self.library_db_path = Path(library_db_path) if library_db_path else None
        self.uploads_dir = Path(uploads_dir) if uploads_dir else None
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._cover_lock = asyncio.Lock()

    # --- connection ---------------------------------------------------------
    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        # wait for the other app's writes instead of failing (set before anything that locks)
        self._conn = await aiosqlite.connect(self.db_path, timeout=10)
        self._conn.row_factory = aiosqlite.Row
        try:
            await self._conn.execute("PRAGMA busy_timeout=10000")
            await self._conn.execute("PRAGMA foreign_keys=ON")
            # Switching to WAL (and the first migration) can report "locked" right away,
            # without waiting, when refsync and explore start together: retry briefly.
            for attempt in range(50):
                try:
                    await self._conn.execute("PRAGMA journal_mode=WAL")
                    await self._migrate()
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e) or attempt == 49:
                        raise
                    await asyncio.sleep(0.1)
        except BaseException:
            await self._conn.close()
            self._conn = None
            raise

    async def _migrate(self) -> None:
        # refsync and explore may start at the same moment on a fresh database:
        # take the write lock first, then check the version inside the transaction.
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            async with self._conn.execute("PRAGMA user_version") as cur:
                version = (await cur.fetchone())[0]
            for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
                for stmt in _statements(script):
                    await self._conn.execute(stmt)
                await self._conn.execute(f"PRAGMA user_version = {i}")
            await self._conn.commit()
        except BaseException:
            await self._conn.rollback()
            raise

    async def disconnect(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Annotation store not connected")
        return self._conn

    @asynccontextmanager
    async def _tx(self):
        # one writer at a time on the shared connection; all-or-nothing
        async with self._lock:
            try:
                yield self.conn
            except BaseException:
                await self.conn.rollback()
                raise
            else:
                await self.conn.commit()

    # --- reads ----------------------------------------------------------------
    async def _projects_for(self, ids: list[str]) -> dict[str, list[dict]]:
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        async with self.conn.execute(
            f"SELECT annotation_id, project_id, project_name FROM annotation_projects "
            f"WHERE annotation_id IN ({q}) ORDER BY project_name",
            ids,
        ) as cur:
            rows = await cur.fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["annotation_id"], []).append(
                {"id": r["project_id"], "name": r["project_name"]}
            )
        return out

    async def _anchors_for(self, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        async with self.conn.execute(
            f"SELECT * FROM annotation_anchors WHERE annotation_id IN ({q})", ids
        ) as cur:
            rows = await cur.fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            rects = json.loads(r["rects"] or "[]")
            out.setdefault(r["annotation_id"], {})[r["fingerprint"]] = {
                "page": r["page"],
                "rects": rects,
                "top": min((x[1] for x in rects), default=0.0),
                "state": r["state"],
            }
        return out

    def _to_dict(self, r, projects, anchors=None) -> dict:
        rects = json.loads(r["rects"] or "[]")
        return {
            "id": r["id"],
            "paper_id": r["paper_id"],
            "kind": r["kind"],
            "color": r["color"],
            "page": r["page"],
            "rects": rects,
            "top": min((x[1] for x in rects), default=0.0),
            "quote": r["quote"],
            "prefix": r["prefix"],
            "suffix": r["suffix"],
            "note": r["note"],
            "image": r["image"],
            "starred": bool(r["starred"]),
            "pdf_fingerprint": r["pdf_fingerprint"],
            "source": r["source"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "projects": projects.get(r["id"], []),
            "anchors": (anchors or {}).get(r["id"], {}),
        }

    async def list(self, paper_ids: Iterable[str], project_id: Optional[str] = None) -> list[dict]:
        ids = [p for p in dict.fromkeys(paper_ids) if p]
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        sql = f"SELECT * FROM annotations WHERE paper_id IN ({q})"
        params: list = list(ids)
        if project_id:
            sql += " AND id IN (SELECT annotation_id FROM annotation_projects WHERE project_id = ?)"
            params.append(project_id)
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        ids = [r["id"] for r in rows]
        projects = await self._projects_for(ids)
        anchors = await self._anchors_for(ids)
        items = [self._to_dict(r, projects, anchors) for r in rows]
        # reading order: page, then position on the page; whole-paper notes first
        items.sort(key=lambda a: (a["page"] or 0, a["top"], a["created_at"]))
        return items

    async def get(self, ann_id: str) -> Optional[dict]:
        async with self.conn.execute("SELECT * FROM annotations WHERE id = ?", (ann_id,)) as cur:
            r = await cur.fetchone()
        if not r:
            return None
        return self._to_dict(
            r, await self._projects_for([ann_id]), await self._anchors_for([ann_id])
        )

    async def summary(self, paper_ids: Iterable[str]) -> dict[str, dict]:
        """Per paper: counts by kind and the cover snip (for cards/badges)."""
        ids = [p for p in dict.fromkeys(paper_ids) if p]
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        out = {p: {"highlight": 0, "note": 0, "snip": 0, "cover": None} for p in ids}
        async with self.conn.execute(
            f"SELECT paper_id, kind, COUNT(*) AS n FROM annotations WHERE paper_id IN ({q}) "
            "GROUP BY paper_id, kind",
            ids,
        ) as cur:
            for r in await cur.fetchall():
                out[r["paper_id"]][r["kind"]] = r["n"]
        async with self.conn.execute(
            f"SELECT paper_id, image FROM annotations WHERE paper_id IN ({q}) AND starred = 1",
            ids,
        ) as cur:
            for r in await cur.fetchall():
                out[r["paper_id"]]["cover"] = r["image"]
        return {p: v for p, v in out.items() if v["highlight"] or v["note"] or v["snip"]}

    def image_path(self, name: str) -> Optional[Path]:
        if not _IMAGE_NAME.match(name or ""):
            return None
        p = self.files_dir / name
        return p if p.exists() else None

    # --- writes ---------------------------------------------------------------
    async def create(self, data: dict, source: Optional[str] = None) -> dict:
        kind = data.get("kind")
        if kind not in KINDS:
            raise AnnotationError(f"kind must be one of {', '.join(KINDS)}")
        paper_id = (data.get("paper_id") or "").strip()
        if not paper_id:
            raise AnnotationError("paper_id is required")
        rects = _clean_rects(data.get("rects"))
        page = data.get("page")
        if page is not None:
            page = int(page)
            if page < 1:
                raise AnnotationError("page is 1-based")
        if kind == "highlight" and (not rects or page is None):
            raise AnnotationError("A highlight needs a page and rects")
        image_name = None
        if kind == "snip":
            if not rects or page is None:
                raise AnnotationError("A snip needs a page and its rectangle")
            raw = _decode_image(data.get("image_data_url"))
            image_name = f"{uuid.uuid4().hex}.png"
            (self.files_dir / image_name).write_bytes(raw)
        if kind == "note" and not (data.get("note") or "").strip():
            raise AnnotationError("A note needs some text")

        ann_id = uuid.uuid4().hex[:16]
        ts = _now()
        try:
            async with self._tx() as db:
                await db.execute(
                    """INSERT INTO annotations (id, paper_id, kind, color, page, rects, quote, prefix,
                           suffix, note, image, pdf_fingerprint, source, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ann_id,
                        paper_id,
                        kind,
                        data.get("color"),
                        page,
                        json.dumps(rects),
                        data.get("quote"),
                        data.get("prefix"),
                        data.get("suffix"),
                        (data.get("note") or None),
                        image_name,
                        data.get("pdf_fingerprint"),
                        source,
                        ts,
                        ts,
                    ),
                )
                await self._set_projects(db, ann_id, data.get("projects") or [])
        except BaseException:
            if image_name:
                (self.files_dir / image_name).unlink(missing_ok=True)
            raise
        created = await self.get(ann_id)
        if kind == "snip" and data.get("starred"):
            # automatic star (e.g. a paper's first snip): don't displace a cover
            # that was uploaded by hand in refsync
            created = await self.update(ann_id, {"starred": True}, replace_uploaded_cover=False)
        return created

    async def _set_projects(self, db, ann_id: str, projects: list) -> None:
        await db.execute("DELETE FROM annotation_projects WHERE annotation_id = ?", (ann_id,))
        rows = []
        for p in projects:
            if isinstance(p, dict) and p.get("id"):
                rows.append((ann_id, str(p["id"]), p.get("name")))
        if rows:
            await db.executemany(
                "INSERT OR IGNORE INTO annotation_projects (annotation_id, project_id, project_name) "
                "VALUES (?, ?, ?)",
                rows,
            )

    # position fields are fixed at creation; other PDF versions go through set_anchor
    EDITABLE = ("note", "color")

    async def update(
        self, ann_id: str, fields: dict, replace_uploaded_cover: bool = True
    ) -> Optional[dict]:
        current = await self.get(ann_id)
        if not current:
            return None
        data = {k: v for k, v in fields.items() if k in self.EDITABLE}
        if current["kind"] == "note" and "note" in data and not (data["note"] or "").strip():
            raise AnnotationError("A note needs some text (delete it instead)")
        star = fields.get("starred")
        if star is not None and current["kind"] != "snip":
            raise AnnotationError("Only snips can be starred")
        async with self._tx() as db:
            if data:
                sets = ", ".join(f"{k} = ?" for k in data)
                await db.execute(
                    f"UPDATE annotations SET {sets}, updated_at = ? WHERE id = ?",
                    (*data.values(), _now(), ann_id),
                )
            if star is not None:
                if star:
                    # one cover per paper
                    await db.execute(
                        "UPDATE annotations SET starred = 0 WHERE paper_id = ? AND starred = 1",
                        (current["paper_id"],),
                    )
                await db.execute(
                    "UPDATE annotations SET starred = ?, updated_at = ? WHERE id = ?",
                    (int(bool(star)), _now(), ann_id),
                )
            if "projects" in fields:
                await self._set_projects(db, ann_id, fields["projects"] or [])
        if star is not None:
            await self.sync_cover(
                current["paper_id"], replace_uploaded=replace_uploaded_cover and bool(star)
            )
        return await self.get(ann_id)

    async def set_anchor(
        self, ann_id: str, fingerprint: str, page: Optional[int], rects, state: str
    ) -> Optional[dict]:
        """Record where an annotation sits in another version of the PDF (or that it's missing)."""
        if not fingerprint:
            raise AnnotationError("fingerprint is required")
        if state not in ("ok", "orphaned"):
            raise AnnotationError("state must be ok or orphaned")
        clean = _clean_rects(rects) if state == "ok" else []
        if state == "ok" and (not clean or not page):
            raise AnnotationError("An anchor needs a page and rects")
        if not await self.get(ann_id):
            return None
        async with self._tx() as db:
            await db.execute(
                """INSERT INTO annotation_anchors (annotation_id, fingerprint, page, rects, state, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(annotation_id, fingerprint) DO UPDATE SET
                     page = excluded.page, rects = excluded.rects, state = excluded.state,
                     updated_at = excluded.updated_at""",
                (
                    ann_id,
                    fingerprint,
                    page if state == "ok" else None,
                    json.dumps(clean),
                    state,
                    _now(),
                ),
            )
        return await self.get(ann_id)

    async def delete(self, ann_id: str) -> bool:
        current = await self.get(ann_id)
        if not current:
            return False
        async with self._tx() as db:
            await db.execute("DELETE FROM annotations WHERE id = ?", (ann_id,))
        if current["image"]:
            (self.files_dir / current["image"]).unlink(missing_ok=True)
        if current["starred"]:
            await self.sync_cover(current["paper_id"])
        return True

    async def rekey(self, old_paper_id: str, new_paper_id: str) -> int:
        """Move annotations to another paper id (same paper stored under another id)."""
        if old_paper_id == new_paper_id:
            return 0
        async with self._tx() as db:
            async with db.execute(
                "SELECT 1 FROM annotations WHERE paper_id = ? AND starred = 1", (new_paper_id,)
            ) as c:
                target_has_cover = await c.fetchone() is not None
            if target_has_cover:  # one cover per paper: the existing one wins
                await db.execute(
                    "UPDATE annotations SET starred = 0 WHERE paper_id = ? AND starred = 1",
                    (old_paper_id,),
                )
            cur = await db.execute(
                "UPDATE annotations SET paper_id = ? WHERE paper_id = ?",
                (new_paper_id, old_paper_id),
            )
        return cur.rowcount

    # --- refsync cover ----------------------------------------------------------
    async def sync_cover(self, paper_id: str, replace_uploaded: bool = False) -> Optional[str]:
        """
        Mirror the paper's starred snip into refsync's `cover_image` (if the
        paper is in the library). A cover uploaded by hand is only replaced when
        `replace_uploaded` (you explicitly starred a snip); its file is kept.
        A cover that came from a snip is cleared again when the snip is
        unstarred or deleted. Returns the cover file name now set in refsync.
        """
        if not (self.library_db_path and self.uploads_dir and self.library_db_path.exists()):
            return None
        async with self.conn.execute(
            "SELECT image FROM annotations WHERE paper_id = ? AND starred = 1", (paper_id,)
        ) as cur:
            row = await cur.fetchone()
        star = row["image"] if row else None
        async with self._cover_lock:  # quick successive stars must land in order
            return await asyncio.to_thread(self._sync_cover_sync, paper_id, star, replace_uploaded)

    def _sync_cover_sync(
        self, paper_id: str, star_image: Optional[str], replace_uploaded: bool = False
    ) -> Optional[str]:
        safe_id = re.sub(r"[^\w.-]", "_", paper_id)
        prefix = f"{safe_id}_snip_"
        conn = sqlite3.connect(self.library_db_path, timeout=10)
        try:
            row = conn.execute(
                "SELECT cover_image FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if row is None:
                return None
            current = row[0]
            uploaded = bool(current) and not current.startswith(prefix)
            if star_image and uploaded and not replace_uploaded:
                return current  # keep the hand-uploaded cover
            if star_image:
                name = prefix + star_image
                self.uploads_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.files_dir / star_image, self.uploads_dir / name)
                new = name
            elif current and current.startswith(prefix):
                new = None
            else:
                return current
            if new != current:
                conn.execute("UPDATE papers SET cover_image = ? WHERE id = ?", (new, paper_id))
                conn.commit()
                if current and current.startswith(prefix):
                    (self.uploads_dir / current).unlink(missing_ok=True)
            return new
        finally:
            conn.close()


def active_projects(explore_db_path: Optional[Path]) -> list[dict]:
    """Explore's non-archived projects (for tagging notes), read-only; [] if none."""
    if not explore_db_path or not Path(explore_db_path).exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{explore_db_path}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute(
                "SELECT id, name FROM projects WHERE archived = 0 ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [{"id": r[0], "name": r[1]} for r in rows]
