#!/usr/bin/env python3
"""
RefSync migration: introduce an internal `id` primary key and split identifiers.

What it does (idempotent — safe to re-run):
  1. Adds columns to `papers`: id, bibcode, source, ads_url
  2. Renames the old `ads_bibcode` column's data into the new `bibcode` column
     (arxiv papers have NULL here; ads-synced papers carry their bibcode).
  3. Backfills `id`:  "arxiv-<arxiv_id with / -> ->"  for every existing row
     (all existing rows are arxiv-sourced) and sets source='arxiv'.
  4. Rebuilds the papers table so `id` is the PRIMARY KEY (SQLite can't change a
     PK in place), preserving all data and column order sanity.
  5. Rebuilds the `papers_fts` virtual table and its triggers to key on `id`
     instead of `arxiv_id`.

Usage:
    python -m refsync.migrate_ids            # uses the configured data dir
    python refsync/migrate_ids.py --db /path/to/library.db

Always back up library.db first (the script makes a .bak copy automatically).
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _make_id_from_arxiv(arxiv_id: str) -> str:
    return "arxiv-" + arxiv_id.replace("/", "-")


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        print(f"No database at {db_path}; nothing to migrate.")
        return

    backup = db_path.with_suffix(db_path.suffix + ".bak")
    shutil.copy2(db_path, backup)
    print(f"Backed up {db_path} -> {backup}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = _columns(conn, "papers")

        # --- Step 1: add new columns if missing -------------------------------
        if "id" not in cols:
            conn.execute("ALTER TABLE papers ADD COLUMN id TEXT")
        if "bibcode" not in cols:
            conn.execute("ALTER TABLE papers ADD COLUMN bibcode TEXT")
        if "source" not in cols:
            conn.execute("ALTER TABLE papers ADD COLUMN source TEXT DEFAULT 'arxiv'")
        if "ads_url" not in cols:
            conn.execute("ALTER TABLE papers ADD COLUMN ads_url TEXT")
        conn.commit()

        # --- Step 2: migrate ads_bibcode -> bibcode ---------------------------
        cols = _columns(conn, "papers")
        if "ads_bibcode" in cols:
            conn.execute(
                "UPDATE papers SET bibcode = ads_bibcode "
                "WHERE bibcode IS NULL AND ads_bibcode IS NOT NULL"
            )
            conn.commit()

        # --- Step 3: backfill id + source -------------------------------------
        rows = conn.execute("SELECT arxiv_id FROM papers WHERE id IS NULL").fetchall()
        for row in rows:
            aid = row["arxiv_id"]
            new_id = _make_id_from_arxiv(aid)
            conn.execute("UPDATE papers SET id = ? WHERE arxiv_id = ?", (new_id, aid))
        conn.execute("UPDATE papers SET source = 'arxiv' WHERE source IS NULL")
        conn.commit()
        print(f"Backfilled id/source for {len(rows)} existing paper(s).")

        # --- Step 4: rebuild papers with id as PRIMARY KEY --------------------
        # Detect whether id is already the PK (re-run safety).
        pk_cols = [r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall() if r[5] > 0]
        if pk_cols != ["id"]:
            _rebuild_papers_table(conn)
            print("Rebuilt papers table with `id` as primary key.")
        else:
            print("papers table already keyed on id; skipping rebuild.")

        # --- Step 5: rebuild FTS + triggers keyed on id -----------------------
        _rebuild_fts(conn)
        print("Rebuilt papers_fts and triggers (keyed on id).")

        conn.commit()
        print("Migration complete.")
    finally:
        conn.close()


def _rebuild_papers_table(conn: sqlite3.Connection) -> None:
    """Recreate `papers` with id TEXT PRIMARY KEY, copy data, swap in place."""
    # Drop FTS triggers first so they don't fire during the copy.
    for trig in ("papers_ai", "papers_ad", "papers_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trig}")

    conn.execute("ALTER TABLE papers RENAME TO papers_old")

    conn.execute(
        """
        CREATE TABLE papers (
            id TEXT PRIMARY KEY,
            arxiv_id TEXT,
            bibcode TEXT,
            source TEXT DEFAULT 'arxiv',
            title TEXT NOT NULL,
            authors TEXT NOT NULL,
            abstract TEXT,
            categories TEXT NOT NULL DEFAULT '[]',
            published TEXT,
            updated TEXT,
            pdf_url TEXT,
            arxiv_url TEXT,
            ads_url TEXT,
            shelves TEXT DEFAULT '[]',
            tags TEXT DEFAULT '[]',
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
            last_citation_sync TEXT,
            local_pdf TEXT
        )
        """
    )

    # Copy over the intersection of columns that exist in the old table.
    old_cols = _columns(conn, "papers_old")
    target_cols = [
        "id",
        "arxiv_id",
        "bibcode",
        "source",
        "title",
        "authors",
        "abstract",
        "categories",
        "published",
        "updated",
        "pdf_url",
        "arxiv_url",
        "ads_url",
        "shelves",
        "tags",
        "status",
        "starred",
        "notes",
        "cover_image",
        "added_at",
        "bibtex",
        "bibtex_source",
        "cite_key",
        "is_published",
        "doi",
        "journal_ref",
        "last_citation_sync",
        "local_pdf",
    ]
    shared = [c for c in target_cols if c in old_cols]
    col_list = ", ".join(shared)
    conn.execute(f"INSERT INTO papers ({col_list}) SELECT {col_list} FROM papers_old")
    conn.execute("DROP TABLE papers_old")


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    """Recreate the FTS virtual table + triggers.

    IMPORTANT: for an external-content FTS5 table the triggers must key on the
    content table's rowid (content_rowid='rowid'), and the special 'delete'
    command must receive old.rowid as its first value. The previous schema keyed
    the 'delete' command on a text column with no rowid, which silently corrupts
    the FTS index on UPDATE/DELETE. We do not index the id/arxiv_id in FTS at all
    — those aren't full-text searched; callers map matches back via rowid.
    """
    for trig in ("papers_ai", "papers_ad", "papers_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trig}")
    conn.execute("DROP TABLE IF EXISTS papers_fts")

    conn.execute(
        """
        CREATE VIRTUAL TABLE papers_fts USING fts5(
            title, authors, abstract, notes,
            content='papers', content_rowid='rowid'
        )
        """
    )
    # Canonical repopulation for external-content tables.
    conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")

    conn.executescript(
        """
        CREATE TRIGGER papers_ai AFTER INSERT ON papers BEGIN
            INSERT INTO papers_fts(rowid, title, authors, abstract, notes)
            VALUES (new.rowid, new.title, new.authors, new.abstract, new.notes);
        END;
        CREATE TRIGGER papers_ad AFTER DELETE ON papers BEGIN
            INSERT INTO papers_fts(papers_fts, rowid, title, authors, abstract, notes)
            VALUES ('delete', old.rowid, old.title, old.authors, old.abstract, old.notes);
        END;
        CREATE TRIGGER papers_au AFTER UPDATE ON papers BEGIN
            INSERT INTO papers_fts(papers_fts, rowid, title, authors, abstract, notes)
            VALUES ('delete', old.rowid, old.title, old.authors, old.abstract, old.notes);
            INSERT INTO papers_fts(rowid, title, authors, abstract, notes)
            VALUES (new.rowid, new.title, new.authors, new.abstract, new.notes);
        END;
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate RefSync DB to id-based pk.")
    parser.add_argument("--db", type=Path, default=None, help="Path to library.db")
    args = parser.parse_args()

    db_path = args.db
    if db_path is None:
        try:
            from refsync.config import settings

            db_path = settings.database_path
        except Exception:
            print("Could not import refsync.config; pass --db explicitly.", file=sys.stderr)
            sys.exit(1)

    migrate(Path(db_path))


if __name__ == "__main__":
    main()
