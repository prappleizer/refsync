#!/usr/bin/env python3
"""
Migration script to add citation fields to existing papers.

Run from the project root:
    python migrate_citations.py

This will:
1. Add new columns to the papers table if they don't exist
2. Generate BibTeX and cite keys for all existing papers
"""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

# === BibTeX Generation Functions (inline to avoid import issues) ===


def generate_cite_key(
    arxiv_id: str, authors: list, published: datetime, existing_keys: set
) -> str:
    """Generate a cite key in format LastName:Year."""
    if authors:
        first_author = authors[0]
        if "," in first_author:
            last_name = first_author.split(",")[0].strip()
        else:
            parts = first_author.strip().split()
            suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "md"}
            last_name = parts[-1]
            for i in range(len(parts) - 1, -1, -1):
                if parts[i].lower().rstrip(".") not in suffixes:
                    last_name = parts[i]
                    break
    else:
        last_name = "Unknown"

    last_name = re.sub(r"[^\w\s-]", "", last_name).strip()
    year = published.year
    base_key = f"{last_name}:{year}"

    if base_key not in existing_keys:
        return base_key

    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = f"{base_key}{suffix}"
        if candidate not in existing_keys:
            return candidate

    return f"{base_key}_{arxiv_id.replace('.', '_')}"


def format_authors_bibtex(authors: list) -> str:
    """Format author list for BibTeX."""
    formatted = []
    for author in authors:
        author = author.strip()
        if "," in author:
            formatted.append(f"{{{author}}}")
        else:
            parts = author.split()
            if len(parts) >= 2:
                last = parts[-1]
                first = " ".join(parts[:-1])
                formatted.append(f"{{{last}}}, {first}")
            else:
                formatted.append(f"{{{author}}}")
    return " and ".join(formatted)


def escape_bibtex(text: str) -> str:
    """Escape special characters for BibTeX."""
    replacements = [("&", r"\&"), ("%", r"\%"), ("_", r"\_"), ("#", r"\#")]
    result = text
    for old, new in replacements:
        result = re.sub(rf"(?<!\\){re.escape(old)}", new, result)
    return result


def generate_arxiv_bibtex(
    arxiv_id: str,
    title: str,
    authors: list,
    categories: list,
    published: datetime,
    cite_key: str,
) -> str:
    """Generate BibTeX entry from arXiv paper metadata."""
    authors_fmt = format_authors_bibtex(authors)
    title_fmt = escape_bibtex(title)
    year = published.year
    primary_class = categories[0] if categories else "astro-ph"

    month_names = [
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    ]
    month = month_names[published.month - 1]

    return f"""@ARTICLE{{{cite_key},
       author = {{{authors_fmt}}},
        title = "{{{title_fmt}}}",
         year = {year},
        month = {month},
       eprint = {{{arxiv_id}}},
archivePrefix = {{arXiv}},
 primaryClass = {{{primary_class}}},
       adsurl = {{https://ui.adsabs.harvard.edu/abs/arXiv:{arxiv_id}}}
}}"""


# === Migration Functions ===


def get_db_path():
    """Find the database in the current directory."""
    return Path(__file__).parent / "library.db"


def add_columns_if_missing(conn: sqlite3.Connection):
    """Add new citation columns to papers table."""
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(papers)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    new_columns = [
        ("bibtex", "TEXT"),
        ("bibtex_source", "TEXT DEFAULT 'arxiv'"),
        ("cite_key", "TEXT"),
        ("is_published", "INTEGER DEFAULT 0"),
        ("doi", "TEXT"),
        ("journal_ref", "TEXT"),
        ("ads_bibcode", "TEXT"),
        ("last_citation_sync", "TEXT"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in existing_columns:
            print(f"  Adding column: {col_name}")
            cursor.execute(f"ALTER TABLE papers ADD COLUMN {col_name} {col_type}")

    conn.commit()


def migrate_papers(conn: sqlite3.Connection):
    """Generate BibTeX for all papers that don't have it."""
    cursor = conn.cursor()

    cursor.execute("""
        SELECT arxiv_id, title, authors, categories, published, cite_key, bibtex
        FROM papers
    """)
    papers = cursor.fetchall()

    # Get existing cite keys to avoid collisions
    existing_keys = set()
    for row in papers:
        if row[5]:  # cite_key
            existing_keys.add(row[5])

    updated = 0
    for row in papers:
        (
            arxiv_id,
            title,
            authors_json,
            categories_json,
            published_str,
            cite_key,
            bibtex,
        ) = row

        # Skip if already has bibtex and cite_key
        if bibtex and cite_key:
            print(f"  Skipping (already done): {arxiv_id}")
            continue

        authors = json.loads(authors_json)
        categories = json.loads(categories_json)
        published = datetime.fromisoformat(published_str)

        # Generate cite key if missing
        if not cite_key:
            cite_key = generate_cite_key(arxiv_id, authors, published, existing_keys)
            existing_keys.add(cite_key)

        # Generate bibtex if missing
        if not bibtex:
            bibtex = generate_arxiv_bibtex(
                arxiv_id, title, authors, categories, published, cite_key
            )

        # Update the paper
        cursor.execute(
            """
            UPDATE papers 
            SET cite_key = ?, bibtex = ?, bibtex_source = 'arxiv'
            WHERE arxiv_id = ?
        """,
            (cite_key, bibtex, arxiv_id),
        )

        updated += 1
        print(f"  Updated: {arxiv_id} -> {cite_key}")

    conn.commit()
    return updated


def main():
    db_path = get_db_path()

    if not db_path.exists():
        print(f"Database not found at {db_path}")
        print("Make sure library.db is in the same folder as this script.")
        return 1

    print(f"Migrating database: {db_path}")

    conn = sqlite3.connect(db_path)

    try:
        print("\n1. Adding new columns...")
        add_columns_if_missing(conn)

        print("\n2. Generating BibTeX for existing papers...")
        updated = migrate_papers(conn)

        print(f"\n✓ Migration complete! Updated {updated} papers.")
        return 0

    except Exception as e:
        print(f"\n✗ Migration failed: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    sys.exit(main())
