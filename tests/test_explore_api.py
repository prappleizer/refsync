"""refsync-explore API end to end, against the fake ADS in fake_ads.py."""

import fake_ads
import httpx
import pytest

from refsync.db import SQLiteDatabase, SQLitePaperRepository, SQLiteShelfRepository
from refsync_explore.config import ExploreSettings
from refsync_explore.main import create_app


@pytest.fixture
async def env(tmp_path, monkeypatch):
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


async def _project(client, name="CGM of dwarfs"):
    r = await client.post("/api/projects", json={"name": name, "description": "lit review"})
    assert r.status_code == 200, (r.status_code, r.text, r.request.url)
    return r.json()["id"]


async def test_pages_render(env):
    client, _, _ = env
    pid = await _project(client)
    assert (await client.get("/")).status_code == 200
    r = await client.get(f"/p/{pid}")
    assert r.status_code == 200 and "CGM of dwarfs" in r.text
    r = await client.get("/p/nope", follow_redirects=False)
    assert r.status_code in (302, 307)


async def test_search_stage_and_seen_state(env):
    client, _, _ = env
    pid = await _project(client)

    r = await client.post(
        f"/api/projects/{pid}/search", json={"q": 'abs:"dwarf"', "sort": "citations"}
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["num_found"] == 4 and res["rate"]["remaining"] == 4987
    cites = [h["citation_count"] for h in res["hits"]]
    assert cites == sorted(cites, reverse=True)
    assert all(h["status"] is None for h in res["hits"])
    first = res["hits"][0]
    assert first["id"] == "arxiv-2302.00002" and first["author_label"] == "Lindqvist & Okafor"

    # stage two, reject one
    ids = [h["id"] for h in res["hits"]]
    r = await client.post(
        f"/api/projects/{pid}/papers",
        json={"paper_ids": ids[:2], "col": "staging", "search_id": res["search_id"]},
    )
    assert r.json()["changed"] == ids[:2]
    await client.post(
        f"/api/projects/{pid}/papers", json={"paper_ids": [ids[2]], "col": "rejected"}
    )

    # re-running the search shows them as already triaged
    res2 = (
        await client.post(
            f"/api/projects/{pid}/search", json={"q": 'abs:"dwarf"', "sort": "citations"}
        )
    ).json()
    status = {h["id"]: (h["status"] or {}).get("col") for h in res2["hits"]}
    assert status[ids[0]] == "staging" and status[ids[2]] == "rejected" and status[ids[3]] is None

    # history: one entry, run twice, 3 papers added from it
    hist = (await client.get(f"/api/projects/{pid}/searches")).json()
    assert len(hist) == 1 and hist[0]["run_count"] == 2 and hist[0]["n_added"] == 2

    # board order keeps search order at the top of staging
    board = (await client.get(f"/api/projects/{pid}/board")).json()
    staging = [c["id"] for c in board["cards"] if c["col"] == "staging"]
    assert staging == ids[:2]
    cards = {c["id"]: c for c in board["cards"]}
    assert cards[ids[0]]["found_via"] == 'abs:"dwarf"' and cards[ids[2]]["found_via"] is None


async def test_paging_and_bad_query(env):
    client, _, _ = env
    pid = await _project(client)
    r = await client.post(f"/api/projects/{pid}/search", json={"q": "*", "start": 0})
    assert r.json()["num_found"] == 10
    r = await client.post(f"/api/projects/{pid}/search", json={"q": "SYNTAX abs:(", "start": 0})
    assert r.status_code == 400 and "parse" in r.json()["detail"]


async def test_card_updates_tags_reorder(env):
    client, _, _ = env
    pid = await _project(client)
    res = (await client.post(f"/api/projects/{pid}/search", json={"q": "*"})).json()
    ids = [h["id"] for h in res["hits"]][:4]
    await client.post(f"/api/projects/{pid}/papers", json={"paper_ids": ids})

    r = await client.patch(
        f"/api/projects/{pid}/papers/{ids[1]}",
        json={
            "col": "probables",
            "one_liner": "Best Lya SB profiles",
            "tags": ["KCWI", "Lya"],
            "read_state": "read",
        },
    )
    card = r.json()
    assert card["col"] == "probables" and card["tags"] == ["KCWI", "Lya"]
    assert card["read_state"] == "read" and card["one_liner"] == "Best Lya SB profiles"

    r = await client.patch(f"/api/projects/{pid}/papers/{ids[0]}", json={"col": "nope"})
    assert r.status_code == 400

    # reorder staging, and drag a card into probables at a chosen position
    await client.post(
        f"/api/projects/{pid}/reorder", json={"col": "staging", "ids": [ids[3], ids[2], ids[0]]}
    )
    await client.post(
        f"/api/projects/{pid}/reorder", json={"col": "probables", "ids": [ids[2], ids[1]]}
    )
    board = (await client.get(f"/api/projects/{pid}/board")).json()
    cols = {c: [x["id"] for x in board["cards"] if x["col"] == c] for c in ("staging", "probables")}
    assert cols == {"staging": [ids[3], ids[0]], "probables": [ids[2], ids[1]]}

    # tags: counts, rename cascades to cards, delete removes from cards
    tags = {t["name"]: t for t in board["tags"]}
    assert tags["KCWI"]["count"] == 1 and tags["KCWI"]["color"].startswith("#")
    await client.patch(f"/api/projects/{pid}/tags/KCWI", json={"new_name": "IFU"})
    await client.delete(f"/api/projects/{pid}/tags/Lya")
    card = (await client.get(f"/api/projects/{pid}/papers/{ids[1]}")).json()["card"]
    assert card["tags"] == ["IFU"]

    # remembered page doesn't count as activity, but is saved
    await client.patch(f"/api/projects/{pid}/papers/{ids[1]}", json={"last_page": 7})
    assert (await client.get(f"/api/projects/{pid}/papers/{ids[1]}")).json()["card"][
        "last_page"
    ] == 7

    assert (await client.delete(f"/api/projects/{pid}/papers/{ids[0]}")).status_code == 200
    projects = (await client.get("/api/projects")).json()
    assert projects[0]["counts"] == {"staging": 1, "probables": 2, "rejected": 0}


async def test_hops(env):
    client, _, _ = env
    pid = await _project(client)
    res = (
        await client.post(
            f"/api/projects/{pid}/search", json={"q": 'identifier:"2023MNRAS.902...12L"'}
        )
    ).json()
    pid_paper = res["hits"][0]["id"]

    cites = (
        await client.post(
            f"/api/projects/{pid}/hop", json={"paper_id": pid_paper, "kind": "citations"}
        )
    ).json()
    assert {h["bibcode"] for h in cites["hits"]} == {
        "2025A&A...903...13V",
        "2025arXiv250400004K",
        "2026ApJ...910...20B",
    }
    assert cites["label"] == "Citations to Lindqvist & Okafor 2023"

    refs = (
        await client.post(
            f"/api/projects/{pid}/hop", json={"paper_id": pid_paper, "kind": "references"}
        )
    ).json()
    assert [h["bibcode"] for h in refs["hits"]] == ["2024ApJ...901...11O"]
    hist = (await client.get(f"/api/projects/{pid}/searches")).json()
    assert hist[0]["label"].startswith("References of")


async def test_add_by_reference(env):
    client, _, _ = env
    pid = await _project(client)
    for ref in (
        "https://arxiv.org/abs/2401.00001",
        "https://ui.adsabs.harvard.edu/abs/2020ApJ...907...17F/abstract",
        "10.9999/fake.6",
    ):
        r = await client.post(f"/api/projects/{pid}/papers/add-ref", json={"ref": ref})
        assert r.status_code == 200, (ref, r.text)
    board = (await client.get(f"/api/projects/{pid}/board")).json()
    assert {c["bibcode"] for c in board["cards"]} == {
        "2024ApJ...901...11O",
        "2020ApJ...907...17F",
        "2021MNRAS.906...16D",
    }
    r = await client.post(f"/api/projects/{pid}/papers/add-ref", json={"ref": "not a paper"})
    assert r.status_code == 400


async def test_pdf_fetch_cache_and_failure(env):
    client, app, cfg = env
    pid = await _project(client)
    res = (await client.post(f"/api/projects/{pid}/search", json={"q": "*"})).json()
    by_bib = {h["bibcode"]: h["id"] for h in res["hits"]}

    arxiv_paper = by_bib["2024ApJ...901...11O"]
    r = await client.get(f"/api/papers/{arxiv_paper}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    assert app.state.pdf_cache.cached(arxiv_paper)
    detail = (await client.get(f"/api/projects/{pid}/papers/{arxiv_paper}")).json()["paper"]
    assert detail["pdf_status"] == "ok" and detail["pdf_source"] == "arXiv" and detail["pdf_cached"]

    # publisher-only paper: the "PDF" is a login page -> clear error, then manual upload
    pub_only = by_bib["2020ApJ...907...17F"]
    r = await client.get(f"/api/papers/{pub_only}/pdf")
    assert r.status_code == 404 and "login" in r.json()["detail"]
    r = await client.post(
        f"/api/papers/{pub_only}/pdf",
        files={"file": ("paper.pdf", fake_ads.make_pdf("uploaded"), "application/pdf")},
    )
    assert r.status_code == 200
    assert (await client.get(f"/api/papers/{pub_only}/pdf")).content.startswith(b"%PDF-")
    r = await client.post(
        f"/api/papers/{pub_only}/pdf", files={"file": ("x.pdf", b"nope", "application/pdf")}
    )
    assert r.status_code == 400


async def test_staging_prefetches_pdfs(env):
    client, app, _ = env
    pid = await _project(client)
    res = (await client.post(f"/api/projects/{pid}/search", json={"q": "dwarf"})).json()
    ids = [h["id"] for h in res["hits"]]
    await client.post(f"/api/projects/{pid}/papers", json={"paper_ids": ids})
    import asyncio

    for _ in range(50):
        if all(app.state.pdf_cache.cached(i) for i in ids):
            break
        await asyncio.sleep(0.05)
    assert all(app.state.pdf_cache.cached(i) for i in ids)


async def test_promote_to_refsync(env):
    client, app, cfg = env
    pid = await _project(client)
    res = (await client.post(f"/api/projects/{pid}/search", json={"q": "*"})).json()
    by_bib = {h["bibcode"]: h["id"] for h in res["hits"]}
    chosen = [
        by_bib["2024ApJ...901...11O"],
        by_bib["2025A&A...903...13V"],
        by_bib["2020ApJ...907...17F"],
    ]
    await client.post(f"/api/projects/{pid}/papers", json={"paper_ids": chosen, "col": "probables"})
    await client.patch(
        f"/api/projects/{pid}/papers/{chosen[0]}",
        json={"one_liner": "Lya haloes in dwarfs", "note": "Fig 4 SB profile", "tags": ["Lya"]},
    )
    await client.get(f"/api/papers/{chosen[0]}/pdf")  # cached -> should be handed over

    # one of them is already in refsync (added earlier via its DOI-less arXiv route)
    rdb = SQLiteDatabase(cfg.refsync_db_path)
    await rdb.connect()
    from refsync_explore import store
    from refsync_explore.services.promote import build_refsync_paper

    row = await store.get_paper(app.state.db.conn, chosen[1])
    await SQLitePaperRepository(rdb).create(build_refsync_paper(row, None))
    await rdb.disconnect()

    board = (await client.get(f"/api/projects/{pid}/board")).json()
    in_refsync = {c["id"]: c["refsync_id"] for c in board["cards"]}
    assert in_refsync[chosen[1]] == chosen[1] and in_refsync[chosen[0]] is None

    r = await client.post(
        f"/api/projects/{pid}/promote",
        json={
            "paper_ids": chosen,
            "shelf": "CGM of dwarfs",
            "carry_tags": True,
            "carry_notes": True,
        },
    )
    results = {x["id"]: x for x in r.json()["results"]}
    assert (
        results[chosen[0]]["status"] == "added" and results[chosen[0]]["cite_key"] == "Okafor:2024"
    )
    assert results[chosen[1]]["status"] == "exists"
    assert results[chosen[2]]["status"] == "added"

    rdb = SQLiteDatabase(cfg.refsync_db_path)
    await rdb.connect()
    repo = SQLitePaperRepository(rdb)
    shelf = await SQLiteShelfRepository(rdb).get_by_name("CGM of dwarfs")
    p = await repo.get(chosen[0])
    assert p.authors[0] == "Adaeze Okafor" and p.arxiv_id == "2401.00001"
    assert p.bibtex.startswith("@ARTICLE{Okafor:2024,") and p.bibtex_source == "ads"
    assert p.is_published and p.journal_ref.startswith("The Astrophysical Journal, 901")
    assert p.notes == "Lya haloes in dwarfs\n\nFig 4 SB profile"
    assert p.tags == ["Lya"] and p.shelves == [shelf.id]
    assert p.local_pdf and (cfg.refsync_pdf_dir / p.local_pdf).exists()
    existing = await repo.get(chosen[1])
    assert existing.shelves == [shelf.id]  # merged into the existing paper
    pub_only = await repo.get(chosen[2])
    assert pub_only.id.startswith("ads-") and pub_only.arxiv_id is None
    await rdb.disconnect()

    board = (await client.get(f"/api/projects/{pid}/board")).json()
    assert all(c["refsync_id"] for c in board["cards"])

    # promoting again is harmless
    r = await client.post(f"/api/projects/{pid}/promote", json={"paper_ids": chosen})
    assert {x["status"] for x in r.json()["results"]} == {"exists"}


async def test_status_and_project_lifecycle(env):
    client, _, _ = env
    st = (await client.get("/api/status")).json()
    assert st["refsync_port"] == 8000 and "ads_key" in st
    pid = await _project(client, "Temp")
    r = await client.patch(f"/api/projects/{pid}", json={"name": "Renamed", "archived": True})
    assert r.json()["name"] == "Renamed" and r.json()["archived"]
    assert all(p["id"] != pid for p in (await client.get("/api/projects")).json())
    assert any(
        p["id"] == pid for p in (await client.get("/api/projects?include_archived=true")).json()
    )
    assert (await client.delete(f"/api/projects/{pid}")).status_code == 200
    assert (await client.get(f"/api/projects/{pid}")).status_code == 404
