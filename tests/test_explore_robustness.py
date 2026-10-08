"""Regression tests for transactional writes, duplicate detection and promote edge cases."""

import asyncio
import json
from datetime import datetime

import fake_ads
import httpx
import pytest

from refsync.db import SQLiteDatabase, SQLitePaperRepository
from refsync.models import Paper
from refsync.services.library import PaperExistsError, add_paper_to_library
from refsync_explore import store
from refsync_explore.config import ExploreSettings
from refsync_explore.db import ExploreDB
from refsync_explore.main import create_app
from refsync_explore.services.ads_search import doc_to_row


@pytest.fixture
async def edb(tmp_path):
    db = ExploreDB(tmp_path / "explore.db")
    await db.connect()
    rows = [doc_to_row(d) for d in fake_ads.CORPUS[:5]]
    await store.upsert_papers(db.conn, rows)
    yield db, [r["id"] for r in rows]
    await db.disconnect()


async def test_failed_write_is_rolled_back_not_committed_later(edb):
    db, ids = edb
    project = await store.create_project(db.conn, "P", None)
    pid = project["id"]
    await store.add_to_project(db.conn, pid, ids[:2], "staging")

    # moves ids[0], then fails on a bad search_id foreign key for a new paper
    with pytest.raises(Exception):
        await store.add_to_project(db.conn, pid, [ids[0], ids[3]], "rejected", search_id=999)

    # an unrelated write must not commit the half-done move
    await store.create_project(db.conn, "Q", None)
    status = await store.project_status(db.conn, pid, ids[:4])
    assert status[ids[0]]["col"] == "staging" and ids[3] not in status


async def test_concurrent_reorders_do_not_interleave(edb):
    db, ids = edb
    pid = (await store.create_project(db.conn, "P", None))["id"]
    await store.add_to_project(db.conn, pid, ids, "staging")
    a, b = list(ids), list(reversed(ids))
    await asyncio.gather(*[store.reorder(db.conn, pid, "staging", o) for o in (a, b, a, b)])
    cards = [c["id"] for c in await store.board(db.conn, pid) if c["col"] == "staging"]
    assert cards in (a, b)  # one whole order wins; never a mix
    positions = [c["position"] for c in await store.board(db.conn, pid)]
    assert len(set(positions)) == len(positions)


async def test_refsync_add_detects_same_paper_under_another_id(tmp_path):
    rdb = SQLiteDatabase(tmp_path / "library.db")
    await rdb.connect()
    repo = SQLitePaperRepository(rdb)
    # stored before fetch_ads_paper learned arXiv ids: ads-<hash> id, no arxiv_id
    old = Paper(id="ads-abc123", bibcode="2023ApJ...950...12P", title="T", authors=["A B"])
    await repo.create(old)
    new = Paper(
        id="arxiv-2301.07041",
        arxiv_id="2301.07041",
        bibcode="2023ApJ...950...12P",
        title="T",
        authors=["A B"],
        published=datetime(2023, 1, 1),
    )
    with pytest.raises(PaperExistsError) as e:
        await add_paper_to_library(repo, new)
    assert e.value.paper_id == "ads-abc123"
    await rdb.disconnect()


@pytest.fixture
async def app_env(tmp_path, monkeypatch):
    fake_ads.install(monkeypatch)
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
            yield client, app, cfg


async def _setup(client, n=2):
    pid = (await client.post("/api/projects", json={"name": "P"})).json()["id"]
    hits = (await client.post(f"/api/projects/{pid}/search", json={"q": "*"})).json()["hits"]
    ids = [h["id"] for h in hits][:n]
    await client.post(f"/api/projects/{pid}/papers", json={"paper_ids": ids, "col": "probables"})
    return pid, ids


async def test_promote_with_stale_catalog_still_merges_shelf(app_env):
    client, app, cfg = app_env
    pid, ids = await _setup(client, 1)
    await client.post(f"/api/projects/{pid}/promote", json={"paper_ids": ids})

    # pretend the catalog can't see the library: add_paper_to_library catches it instead
    app.state.catalog.match = lambda row: None
    await client.patch(f"/api/projects/{pid}/papers/{ids[0]}", json={"tags": ["late-tag"]})
    r = await client.post(
        f"/api/projects/{pid}/promote", json={"paper_ids": ids, "shelf": "Later shelf"}
    )
    assert r.json()["results"][0]["status"] == "exists"
    rdb = SQLiteDatabase(cfg.refsync_db_path)
    await rdb.connect()
    paper = await SQLitePaperRepository(rdb).get(ids[0])
    await rdb.disconnect()
    assert "late-tag" in paper.tags and len(paper.shelves) == 1


async def test_badge_follows_live_library(app_env):
    client, app, cfg = app_env
    pid, ids = await _setup(client, 1)
    await client.post(f"/api/projects/{pid}/promote", json={"paper_ids": ids})
    board = (await client.get(f"/api/projects/{pid}/board")).json()
    assert board["cards"][0]["refsync_id"] == ids[0]

    rdb = SQLiteDatabase(cfg.refsync_db_path)
    await rdb.connect()
    await SQLitePaperRepository(rdb).delete(ids[0])
    await rdb.disconnect()
    board = (await client.get(f"/api/projects/{pid}/board")).json()
    assert board["cards"][0]["refsync_id"] is None


async def test_cross_site_writes_refused(app_env):
    client, _, _ = app_env
    r = await client.post(
        "/api/projects", json={"name": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    r = await client.post("/api/projects", json={"name": "x"}, headers={"Origin": "http://test"})
    assert r.status_code == 200
    assert (
        await client.get("/api/projects", headers={"Origin": "https://evil.example"})
    ).status_code == 200


async def test_pdf_copy_failure_still_reports_added(app_env, monkeypatch):
    client, app, cfg = app_env
    pid, ids = await _setup(client, 1)
    await client.get(f"/api/papers/{ids[0]}/pdf")
    import refsync_explore.services.promote as promote_mod

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(promote_mod.shutil, "copyfile", boom)
    res = (await client.post(f"/api/projects/{pid}/promote", json={"paper_ids": ids})).json()
    r = res["results"][0]
    assert r["status"] == "added" and "PDF couldn't be copied" in r["message"]
    card = (await client.get(f"/api/projects/{pid}/papers/{ids[0]}")).json()["card"]
    assert card["refsync_id"] == ids[0]
    assert json.loads(json.dumps(card))  # serializable
