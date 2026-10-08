"""
Recommendations from the citation graph around a project's papers.

Seeds are the project's Staging (weight 1.0) and Probables (weight 1.5)
papers. For each seed we fetch its reference list and the list of papers
citing it (ADS `reference` / `citation` fields, cached locally), then score
every other paper by how many seeds point at it:

    score = sum of seed weights over seeds that cite the candidate
          + sum of seed weights over seeds the candidate cites

so a paper cited by four of your staged papers, or one that cites five of
them, rises to the top. Each hit says why ("cited by 4 · cites 2").
"""

import json
from datetime import datetime, timedelta
from typing import Optional

from refsync.services.ads import ADSClient, arxiv_id_from_identifiers

from .. import store
from .ads_search import SEARCH_FIELDS, doc_to_row

SEED_WEIGHTS = {"staging": 1.0, "probables": 1.5}
MODES = ("all", "references", "citations")
LINK_TTL = timedelta(days=7)
CHUNK = 50
RECOMMEND_QUERY = "recommend()"


def _chunks(items, n=CHUNK):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _or_query(field: str, values: list[str]) -> str:
    return f"{field}:(" + " OR ".join(f'"{v}"' for v in values) + ")"


def _names(doc: dict) -> set:
    names = {doc.get("bibcode")}
    names.update(doc.get("identifier") or [])
    names.update(doc.get("alternate_bibcode") or [])
    return names


async def fetch_links(db, bibcodes: list[str], client, refresh: bool = False):
    """{bibcode: (refs, cites)} for the given bibcodes, from cache or ADS."""
    bibcodes = list(dict.fromkeys(b for b in bibcodes if b))
    out: dict[str, tuple[list, list]] = {}
    if not bibcodes:
        return out
    cutoff = (datetime.utcnow() - LINK_TTL).isoformat()
    rows = await store._all(
        db,
        f"SELECT * FROM paper_links WHERE bibcode IN ({store._qmarks(len(bibcodes))})",
        bibcodes,
    )
    for r in rows:
        if refresh or r["fetched_at"] < cutoff:
            continue
        out[r["bibcode"]] = (json.loads(r["refs"]), json.loads(r["cites"]))

    missing = [b for b in bibcodes if b not in out]
    fetched = {}
    for chunk in _chunks(missing):
        res = await client.query(
            _or_query("identifier", chunk),
            "bibcode,alternate_bibcode,identifier,reference,citation",
            rows=len(chunk) * 2,
        )
        wanted = set(chunk)
        for doc in res.docs:
            for b in wanted & _names(doc):
                fetched.setdefault(b, (doc.get("reference") or [], doc.get("citation") or []))
    # papers ADS doesn't know get cached as empty so we don't ask every time
    now = datetime.utcnow().isoformat()
    to_store = [(b, *fetched.get(b, ([], []))) for b in missing]
    if to_store:
        async with store.tx(db):
            await db.executemany(
                "INSERT INTO paper_links (bibcode, refs, cites, fetched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(bibcode) DO UPDATE SET refs = excluded.refs, cites = excluded.cites, "
                "fetched_at = excluded.fetched_at",
                [(b, json.dumps(r), json.dumps(c), now) for b, r, c in to_store],
            )
    for b, r, c in to_store:
        out[b] = (r, c)
    return out


def score_candidates(seeds: list[dict], links: dict, mode: str = "all") -> dict[str, dict]:
    """
    seeds: [{"bibcode", "weight", "id"}]. Returns
    {candidate_bibcode: {"score", "cited_by": [seed ids], "cites": [seed ids]}}.
    """
    seed_bibs = {s["bibcode"] for s in seeds}
    cands: dict[str, dict] = {}

    def bump(bib, key, seed):
        if bib in seed_bibs:
            return
        c = cands.setdefault(bib, {"score": 0.0, "cited_by": [], "cites": []})
        if seed["id"] not in c[key]:
            c[key].append(seed["id"])
            c["score"] += seed["weight"]

    for seed in seeds:
        refs, cites = links.get(seed["bibcode"], ([], []))
        if mode in ("all", "references"):
            for b in set(refs):
                bump(b, "cited_by", seed)
        if mode in ("all", "citations"):
            for b in set(cites):
                bump(b, "cites", seed)
    return cands


async def _metadata(client, bibcodes: list[str]) -> dict[str, dict]:
    """ADS docs for candidate bibcodes, keyed by the bibcode we asked for."""
    out = {}
    fl = SEARCH_FIELDS + ",alternate_bibcode"
    for chunk in _chunks(bibcodes):
        res = await client.query(_or_query("identifier", chunk), fl, rows=len(chunk) * 2)
        wanted = set(chunk)
        for doc in res.docs:
            for b in wanted & _names(doc):
                out.setdefault(b, doc)
    return out


async def recommend(
    db,
    pid: str,
    mode: str = "all",
    include_triaged: bool = False,
    min_year: Optional[int] = None,
    limit: int = 100,
    refresh: bool = False,
    client=None,
) -> dict:
    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode}")
    client = client or ADSClient()

    cards = await store.board(db, pid)
    seeds = [
        {"id": c["id"], "bibcode": c["bibcode"], "weight": SEED_WEIGHTS[c["col"]]}
        for c in cards
        if c["col"] in SEED_WEIGHTS and c["bibcode"]
    ]
    in_project = {c["bibcode"]: c for c in cards if c["bibcode"]}
    in_project_arxiv = {c["arxiv_id"] for c in cards if c["arxiv_id"]}
    labels = {c["id"]: f"{c['author_label']} {c['year'] or ''}".strip() for c in cards}

    links = await fetch_links(db, [s["bibcode"] for s in seeds], client, refresh=refresh)
    scored = score_candidates(seeds, links, mode)
    considered = len(scored)

    def keep(bib):
        if not include_triaged and bib in in_project:
            return False
        if min_year and bib[:4].isdigit() and int(bib[:4]) < min_year:
            return False
        return True

    ranked = sorted(
        (b for b in scored if keep(b)),
        key=lambda b: (
            -scored[b]["score"],
            -(len(scored[b]["cited_by"]) + len(scored[b]["cites"])),
            b[:4],
        ),
    )
    # a little headroom: some candidates turn out to be triaged under another id
    top = ranked[: int(limit * 1.2) + 5]
    docs = await _metadata(client, top) if top else {}

    # The same paper can be listed under several bibcodes (arXiv and journal
    # versions in different seeds' reference lists): merge them per paper.
    weights = {s["id"]: s["weight"] for s in seeds}
    seed_ids = set(weights)
    merged: dict[str, dict] = {}
    for bib in top:
        doc = docs.get(bib)
        if not doc:
            continue
        aid = arxiv_id_from_identifiers(doc.get("identifier"))
        if not include_triaged and (
            doc.get("bibcode") in in_project or (aid and aid in in_project_arxiv)
        ):
            continue
        row = doc_to_row(doc)
        if row["id"] in seed_ids:  # a seed, under another of its bibcodes
            continue
        m = merged.setdefault(row["id"], {"row": row, "cited_by": [], "cites": []})
        for key in ("cited_by", "cites"):
            for sid in scored[bib][key]:
                if sid not in m[key]:
                    m[key].append(sid)
    for m in merged.values():
        m["score"] = sum(weights[s] for s in m["cited_by"]) + sum(weights[s] for s in m["cites"])
    best = sorted(
        merged.values(), key=lambda m: (-m["score"], -(len(m["cited_by"]) + len(m["cites"])))
    )[:limit]
    rows = [m["row"] for m in best]
    reasons = {m["row"]["id"]: m for m in best}

    await store.upsert_papers(db, rows)
    ids = [r["id"] for r in rows]
    search_id = await store.record_search(
        db, pid, RECOMMEND_QUERY, mode, "Recommended", considered, ids, 0
    )
    papers = await store.get_papers(db, ids)
    status = await store.project_status(db, pid, ids)
    hits = []
    for i in dict.fromkeys(ids):
        row = papers[i]
        hit = store.paper_summary(row)
        hit["abstract"] = row["abstract"]
        hit["status"] = status.get(i)
        r = reasons[i]
        hit["reason"] = {
            "score": round(r["score"], 2),
            "cited_by": [{"id": s, "label": labels.get(s, s)} for s in r["cited_by"]],
            "cites": [{"id": s, "label": labels.get(s, s)} for s in r["cites"]],
        }
        hits.append(hit)
    hits.sort(key=lambda h: (-h["reason"]["score"], -(h["citation_count"] or 0)))
    return {
        "search_id": search_id,
        "kind": "recs",
        "query": RECOMMEND_QUERY,
        "label": "Recommended",
        "mode": mode,
        "sort": mode,
        "num_found": len(hits),
        "considered": considered,
        "seeds": len(seeds),
        "seeds_without_links": sum(1 for s in seeds if not any(links.get(s["bibcode"], ([], [])))),
        "start": 0,
        "hits": hits,
    }
