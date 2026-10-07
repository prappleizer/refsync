"""
Data access for explore: papers, projects, the triage board, tags and searches.

All functions take the shared aiosqlite connection. Papers are global (one
row per paper, shared across projects); everything about triage lives in
project_papers and is per project.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Iterable, Optional

import aiosqlite

from .text import author_label, display_name

COLUMNS = ("staging", "probables", "rejected")
READ_STATES = ("unseen", "skimmed", "read")

# Tag colors cycle through this palette (works on light and dark backgrounds)
TAG_COLORS = [
    "#8b5cf6",
    "#0ea5e9",
    "#10b981",
    "#f59e0b",
    "#ef4444",
    "#ec4899",
    "#14b8a6",
    "#6366f1",
    "#84cc16",
    "#f97316",
]

PAPER_FIELDS = [
    "id",
    "arxiv_id",
    "bibcode",
    "doi",
    "title",
    "authors",
    "author_count",
    "abstract",
    "year",
    "pubdate",
    "pub",
    "bibstem",
    "volume",
    "page",
    "doctype",
    "citation_count",
    "arxiv_class",
    "esources",
    "fetched_at",
]


def now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


async def _all(db: aiosqlite.Connection, sql: str, params=()) -> list[aiosqlite.Row]:
    async with db.execute(sql, params) as cur:
        return await cur.fetchall()


async def _one(db: aiosqlite.Connection, sql: str, params=()) -> Optional[aiosqlite.Row]:
    async with db.execute(sql, params) as cur:
        return await cur.fetchone()


def _qmarks(n: int) -> str:
    return ",".join("?" * n)


# Every write runs as one transaction under a per-connection lock: it either
# lands completely or is rolled back, and concurrent requests (or background
# PDF fetches) can't interleave their statements on the shared connection.
_write_locks: dict[int, asyncio.Lock] = {}


@asynccontextmanager
async def tx(db: aiosqlite.Connection):
    lock = _write_locks.setdefault(id(db), asyncio.Lock())
    async with lock:
        try:
            yield db
        except BaseException:
            await db.rollback()
            raise
        else:
            await db.commit()


# === Papers ================================================================


async def upsert_papers(db: aiosqlite.Connection, rows: list[dict]) -> None:
    """Insert or refresh paper metadata (PDF cache fields are left untouched)."""
    async with tx(db):
        if not rows:
            return
        cols = ", ".join(PAPER_FIELDS)
        updates = ", ".join(f"{f} = excluded.{f}" for f in PAPER_FIELDS if f != "id")
        await db.executemany(
            f"INSERT INTO papers ({cols}) VALUES ({_qmarks(len(PAPER_FIELDS))}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            [tuple(r.get(f) for f in PAPER_FIELDS) for r in rows],
        )


def paper_summary(row) -> dict:
    """Compact paper dict for cards and result rows."""
    authors = json.loads(row["authors"] or "[]")
    return {
        "id": row["id"],
        "arxiv_id": row["arxiv_id"],
        "bibcode": row["bibcode"],
        "doi": row["doi"],
        "title": row["title"],
        "authors": [display_name(a) for a in authors[:3]],
        "author_count": row["author_count"] or len(authors),
        "author_label": author_label(authors, row["author_count"]),
        "year": row["year"],
        "bibstem": row["bibstem"],
        "pub": row["pub"],
        "doctype": row["doctype"],
        "citation_count": row["citation_count"],
        "esources": json.loads(row["esources"] or "[]"),
        "pdf_status": row["pdf_status"],
    }


def paper_detail(row) -> dict:
    """Full paper dict for the reader/details pane."""
    out = paper_summary(row)
    authors = json.loads(row["authors"] or "[]")
    out.update(
        {
            "authors": [display_name(a) for a in authors],
            "abstract": row["abstract"],
            "pubdate": row["pubdate"],
            "volume": row["volume"],
            "page": row["page"],
            "arxiv_class": json.loads(row["arxiv_class"] or "[]"),
            "pdf_source": row["pdf_source"],
            "pdf_error": row["pdf_error"],
        }
    )
    return out


async def get_paper(db: aiosqlite.Connection, paper_id: str) -> Optional[aiosqlite.Row]:
    return await _one(db, "SELECT * FROM papers WHERE id = ?", (paper_id,))


async def get_papers(db: aiosqlite.Connection, ids: Iterable[str]) -> dict[str, aiosqlite.Row]:
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    rows = await _all(db, f"SELECT * FROM papers WHERE id IN ({_qmarks(len(ids))})", ids)
    return {r["id"]: r for r in rows}


async def set_pdf_state(
    db: aiosqlite.Connection,
    paper_id: str,
    status: str,
    path: Optional[str] = None,
    source: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    async with tx(db):
        await db.execute(
            "UPDATE papers SET pdf_status = ?, pdf_path = ?, pdf_source = ?, pdf_error = ? WHERE id = ?",
            (status, path, source, error, paper_id),
        )


# === Projects ==============================================================


async def list_projects(db: aiosqlite.Connection, include_archived: bool = False) -> list[dict]:
    rows = await _all(
        db,
        f"""
        SELECT p.*,
               SUM(pp.col = 'staging')   AS n_staging,
               SUM(pp.col = 'probables') AS n_probables,
               SUM(pp.col = 'rejected')  AS n_rejected,
               MAX(pp.updated_at)        AS last_activity
        FROM projects p
        LEFT JOIN project_papers pp ON pp.project_id = p.id
        {"" if include_archived else "WHERE p.archived = 0"}
        GROUP BY p.id
        ORDER BY COALESCE(MAX(pp.updated_at), p.updated_at) DESC
        """,
    )
    return [_project_dict(r) for r in rows]


def _project_dict(r) -> dict:
    keys = r.keys()
    return {
        "id": r["id"],
        "name": r["name"],
        "description": r["description"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "archived": bool(r["archived"]),
        "counts": {
            "staging": (r["n_staging"] or 0) if "n_staging" in keys else 0,
            "probables": (r["n_probables"] or 0) if "n_probables" in keys else 0,
            "rejected": (r["n_rejected"] or 0) if "n_rejected" in keys else 0,
        },
        "last_activity": (r["last_activity"] if "last_activity" in keys else None)
        or r["updated_at"],
    }


async def create_project(db: aiosqlite.Connection, name: str, description: Optional[str]) -> dict:
    async with tx(db):
        pid = uuid.uuid4().hex[:8]
        ts = now()
        await db.execute(
            "INSERT INTO projects (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (pid, name, description, ts, ts),
        )
        return await get_project(db, pid)


async def get_project(db: aiosqlite.Connection, pid: str) -> Optional[dict]:
    r = await _one(db, "SELECT * FROM projects WHERE id = ?", (pid,))
    return _project_dict(r) if r else None


async def update_project(db: aiosqlite.Connection, pid: str, fields: dict) -> Optional[dict]:
    async with tx(db):
        allowed = {k: v for k, v in fields.items() if k in ("name", "description", "archived")}
        if allowed:
            if "archived" in allowed:
                allowed["archived"] = int(bool(allowed["archived"]))
            sets = ", ".join(f"{k} = ?" for k in allowed)
            await db.execute(
                f"UPDATE projects SET {sets}, updated_at = ? WHERE id = ?",
                (*allowed.values(), now(), pid),
            )
        return await get_project(db, pid)


async def delete_project(db: aiosqlite.Connection, pid: str) -> bool:
    async with tx(db):
        cur = await db.execute("DELETE FROM projects WHERE id = ?", (pid,))
        return cur.rowcount > 0


async def touch_project(db: aiosqlite.Connection, pid: str) -> None:
    await db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now(), pid))


# === Board =================================================================


async def _tags_by_paper(db: aiosqlite.Connection, pid: str) -> dict[str, list[str]]:
    rows = await _all(
        db,
        "SELECT paper_id, tag FROM project_paper_tags WHERE project_id = ? ORDER BY tag",
        (pid,),
    )
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["paper_id"], []).append(r["tag"])
    return out


def _card(row, tags: list[str]) -> dict:
    card = paper_summary(row)
    card.update(
        {
            "col": row["col"],
            "position": row["position"],
            "one_liner": row["one_liner"],
            "note": row["note"],
            "why_not": row["why_not"],
            "read_state": row["read_state"],
            "last_page": row["last_page"],
            "added_at": row["added_at"],
            "promoted_at": row["promoted_at"],
            "refsync_id": row["refsync_id"],
            "found_via": row["found_via"],
            "tags": tags,
        }
    )
    return card


_CARD_SQL = """
    SELECT p.*, pp.col, pp.position, pp.one_liner, pp.note, pp.why_not, pp.read_state,
           pp.last_page, pp.added_at, pp.promoted_at, pp.refsync_id,
           COALESCE(s.label, s.query) AS found_via
    FROM project_papers pp
    JOIN papers p ON p.id = pp.paper_id
    LEFT JOIN searches s ON s.id = pp.search_id
    WHERE pp.project_id = ?
"""


async def board(db: aiosqlite.Connection, pid: str) -> list[dict]:
    rows = await _all(db, _CARD_SQL + " ORDER BY pp.col, pp.position, pp.added_at", (pid,))
    tags = await _tags_by_paper(db, pid)
    return [_card(r, tags.get(r["id"], [])) for r in rows]


async def get_card(db: aiosqlite.Connection, pid: str, paper_id: str) -> Optional[dict]:
    r = await _one(db, _CARD_SQL + " AND pp.paper_id = ?", (pid, paper_id))
    if not r:
        return None
    tags = await _all(
        db,
        "SELECT tag FROM project_paper_tags WHERE project_id = ? AND paper_id = ? ORDER BY tag",
        (pid, paper_id),
    )
    return _card(r, [t["tag"] for t in tags])


async def project_status(
    db: aiosqlite.Connection, pid: str, paper_ids: list[str]
) -> dict[str, dict]:
    """Triage status of the given papers in this project (for dimming search results)."""
    if not paper_ids:
        return {}
    rows = await _all(
        db,
        f"""SELECT paper_id, col, read_state, one_liner FROM project_papers
            WHERE project_id = ? AND paper_id IN ({_qmarks(len(paper_ids))})""",
        (pid, *paper_ids),
    )
    return {
        r["paper_id"]: {"col": r["col"], "read_state": r["read_state"], "one_liner": r["one_liner"]}
        for r in rows
    }


async def _top_position(db: aiosqlite.Connection, pid: str, col: str) -> float:
    r = await _one(
        db,
        "SELECT MIN(position) AS m FROM project_papers WHERE project_id = ? AND col = ?",
        (pid, col),
    )
    return r["m"] if r and r["m"] is not None else 0.0


async def add_to_project(
    db: aiosqlite.Connection,
    pid: str,
    paper_ids: list[str],
    col: str = "staging",
    search_id: Optional[int] = None,
) -> list[str]:
    """
    Put papers into a column, at the top, keeping the given order.
    Papers already in the project are moved there instead (their notes,
    tags and read state are kept). Returns the ids that changed.
    """
    async with tx(db):
        if col not in COLUMNS:
            raise ValueError(f"Unknown column: {col}")
        ids = list(dict.fromkeys(paper_ids))
        if not ids:
            return []
        existing = await project_status(db, pid, ids)
        top = await _top_position(db, pid, col)
        ts = now()
        changed = []
        for i, paper_id in enumerate(ids):
            pos = top - len(ids) + i
            if paper_id in existing:
                if existing[paper_id]["col"] == col:
                    continue
                await db.execute(
                    """UPDATE project_papers SET col = ?, position = ?, updated_at = ?
                       WHERE project_id = ? AND paper_id = ?""",
                    (col, pos, ts, pid, paper_id),
                )
            else:
                await db.execute(
                    """INSERT INTO project_papers
                       (project_id, paper_id, col, position, search_id, added_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (pid, paper_id, col, pos, search_id, ts, ts),
                )
            changed.append(paper_id)
        await touch_project(db, pid)
        return changed


EDITABLE = ("col", "one_liner", "note", "why_not", "read_state", "last_page")


async def update_card(db: aiosqlite.Connection, pid: str, paper_id: str, fields: dict) -> bool:
    """Patch triage fields. Moving to another column puts the card on top of it."""
    async with tx(db):
        data = {k: v for k, v in fields.items() if k in EDITABLE}
        if "col" in data and data["col"] not in COLUMNS:
            raise ValueError(f"Unknown column: {data['col']}")
        if "read_state" in data and data["read_state"] not in READ_STATES:
            raise ValueError(f"Unknown read state: {data['read_state']}")
        current = await _one(
            db,
            "SELECT col FROM project_papers WHERE project_id = ? AND paper_id = ?",
            (pid, paper_id),
        )
        if not current:
            return False
        if "col" in data and data["col"] != current["col"]:
            data["position"] = await _top_position(db, pid, data["col"]) - 1
        elif "col" in data:
            del data["col"]
        if data:
            # Reading position alone isn't "activity"; don't bump updated_at for it
            bump = set(data) - {"last_page"}
            sets = ", ".join(f"{k} = ?" for k in data)
            if bump:
                sets += ", updated_at = ?"
            params = (*data.values(), *((now(),) if bump else ()), pid, paper_id)
            await db.execute(
                f"UPDATE project_papers SET {sets} WHERE project_id = ? AND paper_id = ?", params
            )
            if bump:
                await touch_project(db, pid)
        return True


async def reorder(db: aiosqlite.Connection, pid: str, col: str, ordered_ids: list[str]) -> None:
    """Set a column's order (and move any listed card into that column)."""
    async with tx(db):
        if col not in COLUMNS:
            raise ValueError(f"Unknown column: {col}")
        ts = now()
        for i, paper_id in enumerate(ordered_ids):
            await db.execute(
                """UPDATE project_papers SET col = ?, position = ?,
                       updated_at = CASE WHEN col != ? THEN ? ELSE updated_at END
                   WHERE project_id = ? AND paper_id = ?""",
                (col, float(i), col, ts, pid, paper_id),
            )


async def remove_from_project(db: aiosqlite.Connection, pid: str, paper_id: str) -> bool:
    async with tx(db):
        cur = await db.execute(
            "DELETE FROM project_papers WHERE project_id = ? AND paper_id = ?", (pid, paper_id)
        )
        return cur.rowcount > 0


async def mark_promoted(db: aiosqlite.Connection, pid: str, paper_id: str, refsync_id: str):
    async with tx(db):
        await db.execute(
            """UPDATE project_papers SET promoted_at = ?, refsync_id = ?
               WHERE project_id = ? AND paper_id = ?""",
            (now(), refsync_id, pid, paper_id),
        )


# === Tags ==================================================================


async def list_tags(db: aiosqlite.Connection, pid: str) -> list[dict]:
    rows = await _all(
        db,
        """SELECT t.name, t.color, COUNT(ppt.paper_id) AS n
           FROM project_tags t
           LEFT JOIN project_paper_tags ppt ON ppt.project_id = t.project_id AND ppt.tag = t.name
           WHERE t.project_id = ?
           GROUP BY t.name ORDER BY t.name""",
        (pid,),
    )
    return [{"name": r["name"], "color": r["color"], "count": r["n"]} for r in rows]


async def ensure_tags(db: aiosqlite.Connection, pid: str, names: Iterable[str]) -> None:
    existing = {t["name"] for t in await list_tags(db, pid)}
    n = len(existing)
    for name in names:
        if name not in existing:
            await db.execute(
                "INSERT INTO project_tags (project_id, name, color) VALUES (?, ?, ?)",
                (pid, name, TAG_COLORS[n % len(TAG_COLORS)]),
            )
            existing.add(name)
            n += 1


async def set_card_tags(db: aiosqlite.Connection, pid: str, paper_id: str, tags: list[str]):
    async with tx(db):
        tags = list(dict.fromkeys(t.strip() for t in tags if t and t.strip()))
        await ensure_tags(db, pid, tags)
        await db.execute(
            "DELETE FROM project_paper_tags WHERE project_id = ? AND paper_id = ?", (pid, paper_id)
        )
        await db.executemany(
            "INSERT INTO project_paper_tags (project_id, paper_id, tag) VALUES (?, ?, ?)",
            [(pid, paper_id, t) for t in tags],
        )
        await db.execute(
            "UPDATE project_papers SET updated_at = ? WHERE project_id = ? AND paper_id = ?",
            (now(), pid, paper_id),
        )


async def update_tag(
    db: aiosqlite.Connection,
    pid: str,
    name: str,
    new_name: Optional[str] = None,
    color: Optional[str] = None,
) -> bool:
    async with tx(db):
        if color:
            await db.execute(
                "UPDATE project_tags SET color = ? WHERE project_id = ? AND name = ?",
                (color, pid, name),
            )
        if new_name and new_name != name:
            # project_paper_tags follows via ON UPDATE CASCADE
            await db.execute(
                "UPDATE project_tags SET name = ? WHERE project_id = ? AND name = ?",
                (new_name, pid, name),
            )
        return True


async def delete_tag(db: aiosqlite.Connection, pid: str, name: str) -> bool:
    async with tx(db):
        cur = await db.execute(
            "DELETE FROM project_tags WHERE project_id = ? AND name = ?", (pid, name)
        )
        return cur.rowcount > 0


# === Searches ==============================================================


async def record_search(
    db: aiosqlite.Connection,
    pid: str,
    query: str,
    sort: str,
    label: Optional[str],
    num_found: int,
    paper_ids: list[str],
    start: int,
) -> int:
    """Save (or refresh) a search in the project's history and store its hits."""
    async with tx(db):
        ts = now()
        row = await _one(
            db,
            "SELECT id FROM searches WHERE project_id = ? AND query = ? AND sort = ?",
            (pid, query, sort),
        )
        if row:
            search_id = row["id"]
            if start == 0:
                await db.execute(
                    """UPDATE searches SET num_found = ?, last_run_at = ?, run_count = run_count + 1,
                           label = COALESCE(?, label) WHERE id = ?""",
                    (num_found, ts, label, search_id),
                )
                await db.execute("DELETE FROM search_hits WHERE search_id = ?", (search_id,))
        else:
            cur = await db.execute(
                """INSERT INTO searches (project_id, query, sort, label, num_found, created_at, last_run_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pid, query, sort, label, num_found, ts, ts),
            )
            search_id = cur.lastrowid
        await db.executemany(
            "INSERT OR IGNORE INTO search_hits (search_id, paper_id, rank) VALUES (?, ?, ?)",
            [(search_id, p, start + i) for i, p in enumerate(paper_ids)],
        )
        return search_id


async def list_searches(db: aiosqlite.Connection, pid: str, limit: int = 50) -> list[dict]:
    rows = await _all(
        db,
        """SELECT s.*, (SELECT COUNT(*) FROM project_papers pp
                        WHERE pp.project_id = s.project_id AND pp.search_id = s.id) AS n_added
           FROM searches s WHERE s.project_id = ?
           ORDER BY s.last_run_at DESC, s.id DESC LIMIT ?""",
        (pid, limit),
    )
    return [
        {
            "id": r["id"],
            "query": r["query"],
            "sort": r["sort"],
            "label": r["label"],
            "num_found": r["num_found"],
            "run_count": r["run_count"],
            "last_run_at": r["last_run_at"],
            "n_added": r["n_added"],
        }
        for r in rows
    ]
