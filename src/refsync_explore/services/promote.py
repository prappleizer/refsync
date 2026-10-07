"""
"Send to refsync": add explore papers to the refsync library.

Papers are built from the ADS metadata explore already has, plus ADS's BibTeX
(one export call per batch), so no arXiv API calls are needed. Insertion goes
through refsync's own add_paper_to_library, so ids, duplicate checks and cite
keys follow exactly the same rules as adding a paper in refsync itself.
"""

import json
import shutil
from datetime import datetime
from typing import Optional

from refsync.db import (
    SQLiteDatabase,
    SQLitePaperRepository,
    SQLiteShelfRepository,
    SQLiteTagRepository,
)
from refsync.models import Paper, PaperUpdate, ShelfCreate, TagCreate
from refsync.services.ads import ADSClient, ADSError, _parse_ads_pubdate
from refsync.services.ads_parse import ads_abstract_url, ads_eprint_pdf_url
from refsync.services.bibtex import (
    generate_arxiv_bibtex,
    generate_cite_key,
    update_cite_key_in_bibtex,
)
from refsync.services.latex import latex_to_text
from refsync.services.library import PaperExistsError, add_paper_to_library
from refsync.services.pdf import generate_pdf_filename

from .. import store
from ..text import display_name


def _journal_ref(row) -> Optional[str]:
    if not row["pub"]:
        return None
    ref = row["pub"]
    if row["volume"]:
        ref += f", {row['volume']}"
    if row["page"]:
        ref += f", {row['page']}"
    return ref


def _ads_record(row) -> dict:
    """Shape of an ADS doc, for ADSClient.is_published."""
    return {
        "pub": row["pub"] or "",
        "doi": row["doi"],
        "volume": row["volume"],
        "doctype": row["doctype"] or "",
    }


def build_refsync_paper(row, bibtex: Optional[str]) -> Paper:
    """explore `papers` row (+ ADS BibTeX) -> refsync Paper, ready to insert."""
    arxiv_id = row["arxiv_id"]
    bibcode = row["bibcode"]
    published = _parse_ads_pubdate(row["pubdate"], int(row["year"]) if row["year"] else None)
    is_pub = ADSClient.is_published(_ads_record(row))
    paper = Paper(
        id=row["id"],
        arxiv_id=arxiv_id,
        bibcode=bibcode,
        source="ads",
        title=latex_to_text(row["title"]),
        authors=[display_name(a) for a in json.loads(row["authors"] or "[]")],
        abstract=latex_to_text(row["abstract"]) if row["abstract"] else None,
        categories=json.loads(row["arxiv_class"] or "[]"),
        published=published,
        updated=published,
        pdf_url=(
            f"https://arxiv.org/pdf/{arxiv_id}"
            if arxiv_id
            else (ads_eprint_pdf_url(bibcode) if bibcode else None)
        ),
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
        ads_url=ads_abstract_url(bibcode) if bibcode else None,
        added_at=datetime.utcnow(),
        doi=row["doi"],
        journal_ref=_journal_ref(row) if is_pub else None,
        is_published=is_pub,
    )
    paper.cite_key = generate_cite_key(paper)
    if bibtex:
        paper.bibtex = update_cite_key_in_bibtex(bibtex, paper.cite_key)
        paper.bibtex_source = "ads"
        paper.last_citation_sync = datetime.utcnow()
    elif arxiv_id:
        paper.bibtex = generate_arxiv_bibtex(paper, paper.cite_key)
        paper.bibtex_source = "arxiv"
    return paper


def _notes(card: dict) -> Optional[str]:
    parts = [p.strip() for p in (card.get("one_liner"), card.get("note")) if p and p.strip()]
    return "\n\n".join(parts) or None


async def promote(
    db,
    catalog,
    cfg,
    pid: str,
    paper_ids: list[str],
    shelf: Optional[str] = None,
    carry_tags: bool = True,
    carry_notes: bool = True,
    pdf_cache=None,
    ads_client=None,
) -> list[dict]:
    """
    Add papers to refsync. Returns one result per paper:
    {"id", "status": "added"|"exists"|"error", "refsync_id", "cite_key"?, "message"?}

    Papers already in refsync are not re-added, but get the shelf/tags.
    """
    project = await store.get_project(db.conn, pid)
    cards = {}
    for paper_id in dict.fromkeys(paper_ids):
        card = await store.get_card(db.conn, pid, paper_id)
        if card:
            cards[paper_id] = card
    rows = await store.get_papers(db.conn, cards)

    # BibTeX for the papers refsync doesn't have yet (one ADS export call)
    new_ids = [i for i in cards if not catalog.match(rows[i])]
    bibcodes = [rows[i]["bibcode"] for i in new_ids if rows[i]["bibcode"]]
    bibtex_by_bibcode: dict[str, str] = {}
    if bibcodes:
        try:
            client = ads_client or ADSClient()
            bibtex_by_bibcode = await client.get_bibtex(bibcodes)
        except ADSError as e:
            # Still add the papers (arXiv BibTeX fallback); a refsync sync fixes it later
            print(f"ADS BibTeX export failed during promote: {e}")

    results = []
    rdb = SQLiteDatabase(cfg.refsync_db_path)
    cfg.refsync_db_path.parent.mkdir(parents=True, exist_ok=True)
    await rdb.connect()
    try:
        papers_repo = SQLitePaperRepository(rdb)
        shelf_id = None
        if shelf and shelf.strip():
            shelf_repo = SQLiteShelfRepository(rdb)
            existing = await shelf_repo.get_by_name(shelf.strip())
            shelf_obj = existing or await shelf_repo.create(
                ShelfCreate(
                    name=shelf.strip(),
                    description=(
                        f"From refsync-explore project: {project['name']}" if project else None
                    ),
                )
            )
            shelf_id = shelf_obj.id

        tag_colors = {t["name"]: t["color"] for t in await store.list_tags(db.conn, pid)}
        tag_repo = SQLiteTagRepository(rdb)

        async def merge_into_existing(existing_id: str, tags: list[str]) -> None:
            current = await papers_repo.get(existing_id)
            if current:
                merged_shelves = list(
                    dict.fromkeys(current.shelves + ([shelf_id] if shelf_id else []))
                )
                merged_tags = list(dict.fromkeys(current.tags + tags))
                await papers_repo.update(
                    existing_id, PaperUpdate(shelves=merged_shelves, tags=merged_tags)
                )

        for paper_id, card in cards.items():
            row = rows[paper_id]
            tags = card["tags"] if carry_tags else []
            try:
                for t in tags:
                    await tag_repo.create(TagCreate(name=t, color=tag_colors.get(t)))

                existing_id = catalog.match(row)
                created = None
                if not existing_id:
                    paper = build_refsync_paper(row, bibtex_by_bibcode.get(row["bibcode"]))
                    paper.tags = list(tags)
                    paper.shelves = [shelf_id] if shelf_id else []
                    if carry_notes:
                        paper.notes = _notes(card)
                    try:
                        created = await add_paper_to_library(papers_repo, paper)
                    except PaperExistsError as e:
                        # The catalog was stale: refsync has it under e.paper_id
                        existing_id = e.paper_id

                if existing_id:
                    await merge_into_existing(existing_id, tags)
                    await store.mark_promoted(db.conn, pid, paper_id, existing_id)
                    results.append({"id": paper_id, "status": "exists", "refsync_id": existing_id})
                    continue

                # The paper is in refsync now: record that before anything optional
                await store.mark_promoted(db.conn, pid, paper_id, created.id)
                result = {
                    "id": paper_id,
                    "status": "added",
                    "refsync_id": created.id,
                    "cite_key": created.cite_key,
                }

                # Hand over the cached PDF so it's already "saved offline" in refsync
                cached = pdf_cache.cached(paper_id) if pdf_cache else None
                if cached:
                    try:
                        cfg.refsync_pdf_dir.mkdir(parents=True, exist_ok=True)
                        filename = generate_pdf_filename(created)
                        shutil.copyfile(cached, cfg.refsync_pdf_dir / filename)
                        await papers_repo.update(created.id, PaperUpdate(local_pdf=filename))
                    except Exception as e:
                        result["message"] = f"Added, but the PDF couldn't be copied: {e}"
                results.append(result)
            except Exception as e:  # report per paper, keep going
                results.append({"id": paper_id, "status": "error", "message": str(e)})
    finally:
        await rdb.disconnect()
        catalog.invalidate()
    return results
