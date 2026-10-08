"""Recommendations from the citation graph, against the fake ADS corpus.

Fake corpus references (paper n cites ...):  2->1, 3->1,2, 4->2,3, 9->5,6, 10->1,2,5
"""

import fake_ads
import httpx
import pytest

from refsync_explore.config import ExploreSettings
from refsync_explore.main import create_app
from refsync_explore.services.recommend import score_candidates

B = {i + 1: d["bibcode"] for i, d in enumerate(fake_ads.CORPUS)}  # paper n -> bibcode


def test_score_candidates_weights_and_directions():
    seeds = [
        {"id": "s4", "bibcode": B[4], "weight": 1.0},
        {"id": "s9", "bibcode": B[9], "weight": 1.5},
        {"id": "s10", "bibcode": B[10], "weight": 1.0},
    ]
    links = {
        B[4]: ([B[2], B[3]], []),
        B[9]: ([B[5], B[6]], []),
        B[10]: ([B[1], B[2], B[5], B[4]], []),  # cites another seed: not a candidate
    }
    s = score_candidates(seeds, links, "all")
    assert B[4] not in s
    assert s[B[5]]["score"] == 2.5 and s[B[5]]["cited_by"] == ["s9", "s10"]
    assert s[B[2]]["score"] == 2.0 and s[B[6]]["score"] == 1.5 and s[B[1]]["score"] == 1.0

    cites_only = score_candidates(seeds, {B[4]: ([B[2]], [B[7]])}, "citations")
    assert set(cites_only) == {B[7]} and cites_only[B[7]]["cites"] == ["s4"]


@pytest.fixture
async def client_env(tmp_path, monkeypatch):
    fake_ads.install(monkeypatch)
    fake_ads.QUERY_LOG.clear()
    cfg = ExploreSettings(
        data_dir=tmp_path / "explore",
        refsync_db_path=tmp_path / "library.db",
        refsync_pdf_dir=tmp_path / "pdfs",
    )
    app = create_app(cfg, pdf_transport=httpx.MockTransport(fake_ads.pdf_handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def _project_with(client, staging, probables):
    pid = (await client.post("/api/projects", json={"name": "P"})).json()["id"]
    hits = (await client.post(f"/api/projects/{pid}/search", json={"q": "*"})).json()["hits"]
    by_bib = {h["bibcode"]: h["id"] for h in hits}
    await client.post(
        f"/api/projects/{pid}/papers", json={"paper_ids": [by_bib[B[n]] for n in staging]}
    )
    await client.post(
        f"/api/projects/{pid}/papers",
        json={"paper_ids": [by_bib[B[n]] for n in probables], "col": "probables"},
    )
    return pid, by_bib


async def test_recommend_ranks_and_explains(client_env):
    client = client_env
    pid, by_bib = await _project_with(client, staging=[4, 10], probables=[9])
    r = await client.post(f"/api/projects/{pid}/recommend", json={})
    assert r.status_code == 200, r.text
    res = r.json()
    order = [h["bibcode"] for h in res["hits"]]
    assert order[:3] == [B[5], B[2], B[6]]
    top = res["hits"][0]
    assert top["reason"]["score"] == 2.5
    assert {s["label"] for s in top["reason"]["cited_by"]} == {
        "Tanaka & Ibsen 2023",
        "Brandt & Okafor 2026",
    }
    assert res["seeds"] == 3 and res["label"] == "Recommended"

    # staging a recommendation records where it came from
    await client.post(
        f"/api/projects/{pid}/papers",
        json={"paper_ids": [top["id"]], "search_id": res["search_id"]},
    )
    card = (await client.get(f"/api/projects/{pid}/papers/{top['id']}")).json()["card"]
    assert card["found_via"] == "Recommended"

    # it's now a seed itself and drops out of the list (unless asked for)
    res2 = (await client.post(f"/api/projects/{pid}/recommend", json={})).json()
    assert B[5] not in [h["bibcode"] for h in res2["hits"]]
    # rejected papers are ignored as seeds and hidden as candidates...
    await client.post(
        f"/api/projects/{pid}/papers", json={"paper_ids": [by_bib[B[2]]], "col": "rejected"}
    )
    res3 = (await client.post(f"/api/projects/{pid}/recommend", json={})).json()
    assert B[2] not in [h["bibcode"] for h in res3["hits"]]
    # ...unless you ask to see triaged papers too
    res4 = (
        await client.post(f"/api/projects/{pid}/recommend", json={"include_triaged": True})
    ).json()
    rejected = [h for h in res4["hits"] if h["bibcode"] == B[2]]
    assert rejected and rejected[0]["status"]["col"] == "rejected"


async def test_recommend_filters_modes_and_caching(client_env):
    client = client_env
    pid, _ = await _project_with(client, staging=[1], probables=[])

    cites = (await client.post(f"/api/projects/{pid}/recommend", json={"mode": "citations"})).json()
    assert {h["bibcode"] for h in cites["hits"]} == {B[2], B[3], B[10]}
    refs = (await client.post(f"/api/projects/{pid}/recommend", json={"mode": "references"})).json()
    assert refs["hits"] == []

    recent = (
        await client.post(
            f"/api/projects/{pid}/recommend", json={"mode": "citations", "min_year": 2025}
        )
    ).json()
    assert {h["bibcode"] for h in recent["hits"]} == {B[3], B[10]}

    # link lists were fetched once and then served from the cache
    link_queries = [q for q in fake_ads.QUERY_LOG if "reference" in q.get("fl", "")]
    assert len(link_queries) == 1
    await client.post(f"/api/projects/{pid}/recommend", json={"refresh": True})
    link_queries = [q for q in fake_ads.QUERY_LOG if "reference" in q.get("fl", "")]
    assert len(link_queries) == 2

    assert (
        await client.post(f"/api/projects/{pid}/recommend", json={"mode": "x"})
    ).status_code == 400


async def test_recommend_with_no_seeds(client_env):
    client = client_env
    pid = (await client.post("/api/projects", json={"name": "Empty"})).json()["id"]
    res = (await client.post(f"/api/projects/{pid}/recommend", json={})).json()
    assert res["hits"] == [] and res["seeds"] == 0


async def test_recommend_merges_alternate_bibcodes(client_env, monkeypatch):
    """One paper cited as its arXiv bibcode by one seed and its journal bibcode by another."""
    from refsync_explore.services import recommend as rec

    client = client_env
    pid, by_bib = await _project_with(client, staging=[4, 10], probables=[])
    arxiv_bib = "2024arXiv240100001X"  # paper 1's preprint bibcode
    assert arxiv_bib in fake_ads.BY_BIBCODE[B[1]]["identifier"]

    async def fake_links(db, bibcodes, client, refresh=False):
        return {B[4]: ([B[1]], []), B[10]: ([arxiv_bib, B[4]], [])}

    monkeypatch.setattr(rec, "fetch_links", fake_links)
    res = (await client.post(f"/api/projects/{pid}/recommend", json={})).json()
    ones = [h for h in res["hits"] if h["bibcode"] == B[1]]
    assert len(ones) == 1 and len(res["hits"]) == 1
    assert ones[0]["reason"]["score"] == 2.0
    assert {s["id"] for s in ones[0]["reason"]["cited_by"]} == {by_bib[B[4]], by_bib[B[10]]}
