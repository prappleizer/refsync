"""ADSClient.query, arXiv-id extraction, and fetch_ads_paper id handling."""

import httpx
import pytest

from refsync.services import ads as ads_mod


def _install(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ads_mod.httpx, "AsyncClient", client)
    monkeypatch.setattr(ads_mod, "get_ads_api_key", lambda: "x" * 20)


def test_arxiv_id_from_identifiers():
    f = ads_mod.arxiv_id_from_identifiers
    assert f(["2023ApJ...950...12P", "arXiv:2301.07041", "10.3847/abc"]) == "2301.07041"
    assert f(["arXiv:astro-ph/0601234v2"]) == "astro-ph/0601234"
    assert f(["2020A&A...641A...6P"]) is None
    assert f(None) is None


async def test_query_returns_docs_count_and_rate_limit(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(
            200,
            json={"response": {"numFound": 1234, "docs": [{"bibcode": "X"}]}},
            headers={"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4812"},
        )

    _install(monkeypatch, handler)
    res = await ads_mod.ADSClient().query(
        'abs:"circumgalactic"', "bibcode", rows=50, start=50, sort="date desc"
    )
    assert res.num_found == 1234 and res.docs == [{"bibcode": "X"}]
    assert res.rate_limit == {"limit": 5000, "remaining": 4812, "reset": None}
    assert seen["start"] == "50" and seen["sort"] == "date desc"


async def test_query_syntax_error_message(monkeypatch):
    _install(
        monkeypatch,
        lambda r: httpx.Response(400, json={"error": {"msg": "syntax error at 'abs:('"}}),
    )
    with pytest.raises(ads_mod.ADSError, match="could not parse"):
        await ads_mod.ADSClient().query("abs:(", "bibcode")


async def test_fetch_ads_paper_uses_arxiv_id_when_known(monkeypatch):
    doc = {
        "bibcode": "2023ApJ...950...12P",
        "title": ["A paper"],
        "author": ["Pasha, Imad"],
        "pubdate": "2023-05-00",
        "pub": "The Astrophysical Journal",
        "volume": "950",
        "page": ["12"],
        "doi": ["10.3847/abc"],
        "doctype": "article",
        "identifier": ["2023ApJ...950...12P", "arXiv:2301.07041"],
    }

    def handler(request):
        if request.url.path.endswith("/search/query"):
            return httpx.Response(200, json={"response": {"numFound": 1, "docs": [doc]}})
        return httpx.Response(200, json={"export": "@ARTICLE{2023ApJ...950...12P,\n}"})

    _install(monkeypatch, handler)
    paper = await ads_mod.fetch_ads_paper("2023ApJ...950...12P")
    assert paper.id == "arxiv-2301.07041"
    assert paper.arxiv_id == "2301.07041" and paper.bibcode == "2023ApJ...950...12P"
    assert paper.pdf_url == "https://arxiv.org/pdf/2301.07041"
    assert paper.is_published and paper.bibtex.startswith("@ARTICLE{Pasha:2023,")
