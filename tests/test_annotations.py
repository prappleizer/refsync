"""Shared annotations: one store used by both refsync and refsync-explore."""

import base64
import struct
import types
import uuid
import zlib

import fake_ads
import httpx
import pytest

from refsync.config import settings as rs_settings
from refsync.db import SQLiteDatabase, SQLitePaperRepository
from refsync.models import Paper
from refsync_explore.config import ExploreSettings
from refsync_explore.main import create_app


def png_data_url(w=2, h=2) -> str:
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode()


@pytest.fixture
async def apps(tmp_path, monkeypatch):
    """explore and refsync, sharing refsync's (test) data dir like they do for real."""
    fake_ads.install(monkeypatch)
    from refsync.main import app as rs_app

    cfg = ExploreSettings(data_dir=tmp_path / "explore")  # everything else: refsync defaults
    ex_app = create_app(cfg, pdf_transport=httpx.MockTransport(fake_ads.pdf_handler))
    async with ex_app.router.lifespan_context(ex_app), rs_app.router.lifespan_context(rs_app):
        # for real both apps find explore's DB at the same default path; here it's a temp dir
        rs_app.state.explore_db_path = cfg.db_path
        ex = httpx.AsyncClient(transport=httpx.ASGITransport(app=ex_app), base_url="http://ex")
        rs = httpx.AsyncClient(transport=httpx.ASGITransport(app=rs_app), base_url="http://rs")
        async with ex, rs:
            yield types.SimpleNamespace(ex=ex, rs=rs, ex_app=ex_app, rs_app=rs_app, cfg=cfg)


def pid():
    return f"arxiv-9999.{uuid.uuid4().hex[:5]}"


async def test_highlight_made_in_explore_shows_in_refsync(apps):
    paper = pid()
    r = await apps.ex.post(
        "/api/annotations",
        json={
            "paper_id": paper,
            "kind": "highlight",
            "color": "yellow",
            "page": 3,
            "rects": [[0.1, 0.5, 0.6, 0.52], [0.1, 0.52, 0.4, 0.54]],
            "quote": "the covering fraction drops beyond 50 kpc",
            "note": "key result",
            "pdf_fingerprint": "abc",
        },
    )
    assert r.status_code == 200, r.text
    await apps.ex.post(
        "/api/annotations",
        json={
            "paper_id": paper,
            "kind": "highlight",
            "page": 1,
            "rects": [[0.1, 0.9, 0.2, 0.95]],
            "quote": "intro",
        },
    )
    await apps.rs.post(
        "/api/annotations", json={"paper_id": paper, "kind": "note", "note": "whole-paper note"}
    )

    items = (await apps.rs.get(f"/api/annotations?paper_id={paper}")).json()
    assert [a["kind"] for a in items] == ["note", "highlight", "highlight"]  # reading order
    assert [a["page"] for a in items] == [None, 1, 3]
    assert items[2]["source"] == "explore" and items[0]["source"] == "refsync"
    assert items[2]["note"] == "key result" and items[2]["top"] == 0.5

    # edit from refsync, visible in explore
    await apps.rs.patch(
        f"/api/annotations/{items[2]['id']}", json={"note": "key result (Fig. 4)", "color": "green"}
    )
    again = (await apps.ex.get(f"/api/annotations?paper_id={paper}")).json()
    assert again[2]["note"] == "key result (Fig. 4)" and again[2]["color"] == "green"
    assert (await apps.ex.delete(f"/api/annotations/{again[1]['id']}")).status_code == 200
    assert len((await apps.rs.get(f"/api/annotations?paper_id={paper}")).json()) == 2


async def test_validation(apps):
    paper = pid()
    bad = [
        {"paper_id": paper, "kind": "highlight", "page": 1},  # no rects
        {"paper_id": paper, "kind": "highlight", "page": 1, "rects": [[0, 0, 2, 2]]},  # off page
        {
            "paper_id": paper,
            "kind": "snip",
            "page": 1,
            "rects": [[0, 0, 0.5, 0.5]],
            "image_data_url": "data:image/png;base64,AAAA",
        },
        {"paper_id": paper, "kind": "note", "note": "  "},
        {"paper_id": paper, "kind": "doodle"},
    ]
    for body in bad:
        r = await apps.ex.post("/api/annotations", json=body)
        assert r.status_code in (400, 422), (body, r.text)
    note = (
        await apps.ex.post(
            "/api/annotations", json={"paper_id": paper, "kind": "note", "note": "x"}
        )
    ).json()
    r = await apps.ex.patch(f"/api/annotations/{note['id']}", json={"starred": True})
    assert r.status_code == 400  # only snips can be the cover
    assert (await apps.ex.get("/api/annotations/files/..%2Flibrary.db")).status_code == 404


async def test_snips_star_gallery_and_files(apps):
    paper = pid()
    mk = lambda: apps.ex.post(  # noqa: E731
        "/api/annotations",
        json={
            "paper_id": paper,
            "kind": "snip",
            "page": 2,
            "rects": [[0.1, 0.2, 0.9, 0.6]],
            "image_data_url": png_data_url(),
        },
    )
    a = (await mk()).json()
    b = (await mk()).json()
    img = await apps.rs.get(f"/api/annotations/files/{a['image']}")
    assert img.status_code == 200 and img.content.startswith(b"\x89PNG")

    await apps.ex.patch(f"/api/annotations/{a['id']}", json={"starred": True})
    await apps.ex.patch(
        f"/api/annotations/{b['id']}", json={"starred": True}
    )  # one cover per paper
    items = {x["id"]: x for x in (await apps.ex.get(f"/api/annotations?paper_id={paper}")).json()}
    assert not items[a["id"]]["starred"] and items[b["id"]]["starred"]

    summ = (await apps.ex.get(f"/api/annotations/summary?paper_id={paper},nothing-here")).json()
    assert summ == {paper: {"highlight": 0, "note": 0, "snip": 2, "cover": b["image"]}}

    await apps.ex.delete(f"/api/annotations/{b['id']}")
    assert not (apps.cfg.annotations_dir / b["image"]).exists()
    assert (await apps.ex.get(f"/api/annotations/summary?paper_id={paper}")).json()[paper][
        "cover"
    ] is None


async def test_project_tags_and_filter(apps):
    paper = pid()
    proj = (await apps.ex.post("/api/projects", json={"name": "CGM of dwarfs"})).json()
    listed = (await apps.rs.get("/api/annotations/projects")).json()
    assert {"id": proj["id"], "name": "CGM of dwarfs"} in listed

    tagged = (
        await apps.ex.post(
            "/api/annotations",
            json={"paper_id": paper, "kind": "note", "note": "use for intro", "projects": [proj]},
        )
    ).json()
    await apps.ex.post(
        "/api/annotations", json={"paper_id": paper, "kind": "note", "note": "general"}
    )
    assert tagged["projects"] == [{"id": proj["id"], "name": "CGM of dwarfs"}]

    only = (await apps.rs.get(f"/api/annotations?paper_id={paper}&project={proj['id']}")).json()
    assert [a["note"] for a in only] == ["use for intro"]
    r = await apps.ex.patch(f"/api/annotations/{tagged['id']}", json={"projects": []})
    assert r.json()["projects"] == []


async def _library_paper(paper_id, cover=None):
    rdb = SQLiteDatabase(rs_settings.database_path)
    await rdb.connect()
    repo = SQLitePaperRepository(rdb)
    await repo.create(
        Paper(
            id=paper_id,
            arxiv_id=paper_id[6:],
            title="T",
            authors=["A B"],
            pdf_url=f"https://arxiv.org/pdf/{paper_id[6:]}",
        )
    )
    if cover:
        await repo.set_cover(paper_id, cover)
    await rdb.disconnect()


async def _library_get(paper_id):
    rdb = SQLiteDatabase(rs_settings.database_path)
    await rdb.connect()
    p = await SQLitePaperRepository(rdb).get(paper_id)
    await rdb.disconnect()
    return p


async def test_starred_snip_becomes_refsync_cover(apps):
    paper = pid()
    await _library_paper(paper)
    snip = (
        await apps.ex.post(
            "/api/annotations",
            json={
                "paper_id": paper,
                "kind": "snip",
                "page": 1,
                "rects": [[0, 0, 1, 0.5]],
                "image_data_url": png_data_url(),
                "starred": True,
            },
        )
    ).json()
    p = await _library_get(paper)
    assert p.cover_image and p.cover_image.startswith(f"{paper}_snip_")
    assert (rs_settings.uploads_dir / p.cover_image).exists()
    assert (await apps.rs.get(f"/uploads/{p.cover_image}")).status_code == 200

    await apps.rs.patch(f"/api/annotations/{snip['id']}", json={"starred": False})
    assert (await _library_get(paper)).cover_image is None

    # a cover uploaded by hand is never replaced by un-starring
    other = pid()
    await _library_paper(other, cover="hand_made.png")
    s2 = (
        await apps.rs.post(
            "/api/annotations",
            json={
                "paper_id": other,
                "kind": "snip",
                "page": 1,
                "rects": [[0, 0, 1, 1]],
                "image_data_url": png_data_url(),
            },
        )
    ).json()
    await apps.rs.patch(f"/api/annotations/{s2['id']}", json={"starred": False})
    assert (await _library_get(other)).cover_image == "hand_made.png"


async def test_refsync_pdf_endpoint_saves_to_library(apps, monkeypatch):
    import refsync.services.pdf as pdf_mod

    shim = types.ModuleType("httpx_pdf")
    shim.__dict__.update(httpx.__dict__)
    shim.AsyncClient = lambda *a, **k: httpx.AsyncClient(
        *a, **{**k, "transport": httpx.MockTransport(fake_ads.pdf_handler)}
    )
    monkeypatch.setattr(pdf_mod, "httpx", shim)

    paper = pid()
    await _library_paper(paper)
    r = await apps.rs.get(f"/api/papers/{paper}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    p = await _library_get(paper)
    assert p.local_pdf and (rs_settings.pdf_dir / p.local_pdf).exists()

    # failures say what to do; uploading fixes it
    bad = f"ads-{uuid.uuid4().hex[:8]}"
    rdb = SQLiteDatabase(rs_settings.database_path)
    await rdb.connect()
    await SQLitePaperRepository(rdb).create(
        Paper(
            id=bad,
            bibcode="2020ApJ...907...17F",
            title="T",
            authors=["A B"],
            pdf_url="https://publisher.example/PUB_PDF",
        )
    )
    await rdb.disconnect()
    r = await apps.rs.get(f"/api/papers/{bad}/pdf")
    assert r.status_code == 404 and "upload" in r.json()["detail"]
    r = await apps.rs.post(
        f"/api/papers/{bad}/pdf",
        files={"file": ("p.pdf", fake_ads.make_pdf("x"), "application/pdf")},
    )
    assert r.status_code == 200 and r.json()["local_pdf"]
    assert (await apps.rs.get(f"/api/papers/{bad}/pdf")).content.startswith(b"%PDF-")
    assert (await apps.rs.get(f"/paper/{bad}/read")).status_code == 200


async def test_promote_brings_cover_and_rekeys(apps):
    ex = apps.ex
    proj = (await ex.post("/api/projects", json={"name": "P"})).json()["id"]
    hits = (await ex.post(f"/api/projects/{proj}/search", json={"q": "*"})).json()["hits"]
    target = next(h for h in hits if h["bibcode"] == "2023ApJ...909...19T")
    await ex.post(
        f"/api/projects/{proj}/papers", json={"paper_ids": [target["id"]], "col": "probables"}
    )
    snip = (
        await ex.post(
            "/api/annotations",
            json={
                "paper_id": target["id"],
                "kind": "snip",
                "page": 1,
                "rects": [[0, 0, 1, 1]],
                "image_data_url": png_data_url(),
                "starred": True,
            },
        )
    ).json()
    res = (
        await ex.post(f"/api/projects/{proj}/promote", json={"paper_ids": [target["id"]]})
    ).json()
    assert res["results"][0]["status"] == "added"
    p = await _library_get(target["id"])
    assert p.cover_image and p.cover_image.endswith(snip["image"])


async def test_positions_on_other_pdf_versions(apps):
    """The original position never changes; other PDF versions get their own anchor."""
    paper = pid()
    proj = (await apps.ex.post("/api/projects", json={"name": "Anchors"})).json()
    h = (
        await apps.ex.post(
            "/api/annotations",
            json={
                "paper_id": paper,
                "kind": "highlight",
                "page": 3,
                "rects": [[0.1, 0.5, 0.6, 0.52]],
                "quote": "covering fraction",
                "prefix": "we measure the ",
                "suffix": " out to 100 kpc",
                "pdf_fingerprint": "explore-pdf",
                "projects": [proj],
            },
        )
    ).json()
    r = await apps.rs.put(
        f"/api/annotations/{h['id']}/anchor",
        json={
            "fingerprint": "refsync-pdf",
            "state": "ok",
            "page": 4,
            "rects": [[0.2, 0.1, 0.5, 0.12]],
        },
    )
    assert r.status_code == 200, r.text
    await apps.rs.put(
        f"/api/annotations/{h['id']}/anchor", json={"fingerprint": "third-pdf", "state": "orphaned"}
    )
    got = (await apps.ex.get(f"/api/annotations?paper_id={paper}")).json()[0]
    assert got["page"] == 3 and got["rects"] == [[0.1, 0.5, 0.6, 0.52]]
    assert got["pdf_fingerprint"] == "explore-pdf"
    assert got["anchors"]["refsync-pdf"] == {
        "page": 4,
        "rects": [[0.2, 0.1, 0.5, 0.12]],
        "top": 0.1,
        "state": "ok",
    }
    assert got["anchors"]["third-pdf"]["state"] == "orphaned"

    # PATCH can't move it, and null fields mean "unchanged" (tags are kept)
    r = await apps.rs.patch(
        f"/api/annotations/{h['id']}",
        json={"page": 9, "rects": [[0, 0, 1, 1]], "note": None, "projects": None, "color": "blue"},
    )
    assert r.status_code == 200
    got = r.json()
    assert got["page"] == 3 and got["color"] == "blue" and len(got["projects"]) == 1

    bad = await apps.rs.put(
        f"/api/annotations/{h['id']}/anchor", json={"fingerprint": "x", "state": "ok"}
    )
    assert bad.status_code == 400
    missing = await apps.rs.put(
        "/api/annotations/nope/anchor", json={"fingerprint": "x", "state": "orphaned"}
    )
    assert missing.status_code == 404

    # deleting the highlight deletes its anchors
    await apps.ex.delete(f"/api/annotations/{h['id']}")
    store = apps.ex_app.state.annotations
    async with store.conn.execute(
        "SELECT COUNT(*) FROM annotation_anchors WHERE annotation_id = ?", (h["id"],)
    ) as cur:
        assert (await cur.fetchone())[0] == 0


async def test_concurrent_first_start_migrates_once(tmp_path):
    from refsync.annotations import AnnotationStore
    from refsync.annotations.store import MIGRATIONS

    a = AnnotationStore(tmp_path / "a.db", tmp_path / "files")
    b = AnnotationStore(tmp_path / "a.db", tmp_path / "files")
    import asyncio

    try:
        await asyncio.gather(a.connect(), b.connect())
        async with a.conn.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] == len(MIGRATIONS)
    finally:
        await a.disconnect()
        await b.disconnect()


def _snip(paper, starred=False):
    return {
        "paper_id": paper,
        "kind": "snip",
        "page": 1,
        "rects": [[0, 0, 1, 0.5]],
        "image_data_url": png_data_url(),
        "starred": starred,
    }


async def test_uploaded_cover_kept_by_auto_star_replaced_by_explicit_star(apps):
    paper = pid()
    await _library_paper(paper, cover="hand_made.png")
    first = (await apps.rs.post("/api/annotations", json=_snip(paper, starred=True))).json()
    assert first["starred"]  # the paper's first snip is starred automatically...
    assert (await _library_get(paper)).cover_image == "hand_made.png"  # ...but keeps your cover
    second = (await apps.rs.post("/api/annotations", json=_snip(paper))).json()
    await apps.rs.patch(f"/api/annotations/{second['id']}", json={"starred": True})
    cover = (await _library_get(paper)).cover_image
    assert cover == f"{paper}_snip_{second['image']}"  # starring it yourself does replace it


async def test_rekey_keeps_one_cover(apps):
    store = apps.ex_app.state.annotations
    old, new = pid(), pid()
    a = await store.create(_snip(old, starred=True), source="explore")
    b = await store.create(_snip(new, starred=True), source="refsync")
    assert a["starred"] and b["starred"]
    assert await store.rekey(old, new) == 1
    items = await store.list([new])
    assert [x["id"] for x in items if x["starred"]] == [b["id"]]


async def test_promote_existing_paper_gets_pdf_and_notes(apps):
    ex = apps.ex
    proj = (await ex.post("/api/projects", json={"name": "P"})).json()["id"]
    hits = (await ex.post(f"/api/projects/{proj}/search", json={"q": "*"})).json()["hits"]
    target = next(h for h in hits if h["arxiv_id"])
    await ex.post(f"/api/projects/{proj}/papers", json={"paper_ids": [target["id"]]})
    assert (await ex.get(f"/api/papers/{target['id']}/pdf")).status_code == 200  # cached here
    note = (
        await ex.post(
            "/api/annotations", json={"paper_id": target["id"], "kind": "note", "note": "n"}
        )
    ).json()

    # refsync already has this paper, under its own id and without a PDF
    rs_id = f"ads-{uuid.uuid4().hex[:8]}"
    rdb = SQLiteDatabase(rs_settings.database_path)
    await rdb.connect()
    await SQLitePaperRepository(rdb).create(
        Paper(
            id=rs_id,
            arxiv_id=target["arxiv_id"],
            title="Same paper",
            authors=["A B"],
            pdf_url=f"https://arxiv.org/pdf/{target['arxiv_id']}",
        )
    )
    await rdb.disconnect()
    apps.ex_app.state.catalog.invalidate()

    res = (
        await ex.post(f"/api/projects/{proj}/promote", json={"paper_ids": [target["id"]]})
    ).json()["results"][0]
    assert res["status"] == "exists" and res["refsync_id"] == rs_id, res
    assert "message" not in res
    p = await _library_get(rs_id)
    assert p.local_pdf and (rs_settings.pdf_dir / p.local_pdf).exists()
    moved = (await apps.rs.get(f"/api/annotations?paper_id={rs_id}")).json()
    assert [a["id"] for a in moved] == [note["id"]]


async def test_pdf_cache_settled_waits_for_running_download(tmp_path):
    import asyncio

    from refsync_explore.services.pdf_cache import PdfCache

    cache = PdfCache(db=None, pdf_dir=tmp_path)
    assert await cache.settled("p1") is None  # nothing cached, nothing running
    lock = cache._locks.setdefault("p1", asyncio.Lock())
    await lock.acquire()  # a prefetch is downloading p1
    waiter = asyncio.create_task(cache.settled("p1", timeout=2))
    await asyncio.sleep(0.05)
    cache.path_for("p1").write_bytes(b"%PDF-1.4 test")
    lock.release()
    assert await waiter == cache.path_for("p1")
