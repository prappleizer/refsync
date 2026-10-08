"""
A tiny fake ADS for tests: an invented corpus (fictional authors and papers)
plus an httpx handler that answers /search/query and /export/bibtex.

Understands the queries explore sends: identifier:(...), doi:"...",
references(bibcode:"X"), citations(bibcode:"X"), similar(bibcode:"X"), and
plain words (matched against title + abstract). Supports start/rows/sort.
"""

import json
import re
from urllib.parse import parse_qs, urlparse

import httpx


def _doc(n, bibcode, arxiv, title, authors, year, pub, bibstem, cites, refs=(), **extra):
    ids = [bibcode]
    if arxiv:
        ids += [f"arXiv:{arxiv}", f"{year}arXiv{arxiv.replace('.', '')}X"]
    doc = {
        "bibcode": bibcode,
        "title": [title],
        "author": authors,
        "author_count": len(authors),
        "abstract": extra.pop(
            "abstract",
            f"We study {title.lower()}. Using integral-field spectroscopy we measure "
            f"the covering fraction and surface brightness out to 100 kpc (paper {n}).",
        ),
        "year": str(year),
        "pubdate": f"{year}-0{(n % 9) + 1}-00",
        "pub": pub,
        "bibstem": [bibstem],
        "volume": str(900 + n) if bibstem != "arXiv" else None,
        "page": [str(10 + n)],
        "doi": [f"10.9999/fake.{n}"] if bibstem != "arXiv" else None,
        "doctype": "article" if bibstem != "arXiv" else "eprint",
        "citation_count": cites,
        "identifier": ids,
        "arxiv_class": ["astro-ph.GA"],
        "esources": (["EPRINT_PDF", "EPRINT_HTML"] if arxiv else [])
        + (["PUB_PDF", "PUB_HTML"] if bibstem != "arXiv" else []),
        "references": list(refs),
    }
    doc.update(extra)
    return doc


CORPUS = [
    _doc(
        1,
        "2024ApJ...901...11O",
        "2401.00001",
        "Lyman-alpha haloes around dwarf galaxies at z~3",
        ["Okafor, Adaeze", "Lindqvist, Maja", "Tanaka, Ren"],
        2024,
        "The Astrophysical Journal",
        "ApJ",
        42,
    ),
    _doc(
        2,
        "2023MNRAS.902...12L",
        "2302.00002",
        "The cool circumgalactic medium of star-forming dwarfs",
        ["Lindqvist, Maja", "Okafor, Adaeze"],
        2023,
        "Monthly Notices of the Royal Astronomical Society",
        "MNRAS",
        88,
        refs=["2024ApJ...901...11O"],
    ),
    _doc(
        3,
        "2025A&A...903...13V",
        "2503.00003",
        "Mapping H$\\alpha$ emission in the outskirts of nearby dwarf galaxies",
        ["van Houten, Pieter", "Ferreira, Lucia", "Brandt, Jonas"],
        2025,
        "Astronomy and Astrophysics",
        "A&A",
        7,
        refs=["2024ApJ...901...11O", "2023MNRAS.902...12L"],
    ),
    _doc(
        4,
        "2025arXiv250400004K",
        "2504.00004",
        "Kinematics of the circumgalactic medium with KCWI",
        ["Kowalczyk, Ewa", "Mensah, Kofi"],
        2025,
        "arXiv e-prints",
        "arXiv",
        3,
        refs=["2023MNRAS.902...12L", "2025A&A...903...13V"],
    ),
    _doc(
        5,
        "2022ApJ...905...15M",
        "2205.00005",
        "Metal-enriched outflows from low-mass galaxies",
        ["Mensah, Kofi", "Delgado, Rosa", "Kowalczyk, Ewa", "Ibsen, Tor"],
        2022,
        "The Astrophysical Journal",
        "ApJ",
        130,
    ),
    _doc(
        6,
        "2021MNRAS.906...16D",
        "2106.00006",
        "Covering fractions of O VI around sub-L* galaxies",
        ["Delgado, Rosa"],
        2021,
        "Monthly Notices of the Royal Astronomical Society",
        "MNRAS",
        75,
    ),
    _doc(
        7,
        "2020ApJ...907...17F",
        None,
        "A deep narrowband survey of the Local Volume",
        ["Ferreira, Lucia", "de la Cruz, Mateo"],
        2020,
        "The Astrophysical Journal",
        "ApJ",
        51,
    ),
    _doc(
        8,
        "2024AJ....908...18E",
        "2408.00008",
        "Survey design for faint diffuse emission",
        ["Euclid Collaboration", "Brandt, Jonas"],
        2024,
        "The Astronomical Journal",
        "AJ",
        19,
    ),
    _doc(
        9,
        "2023ApJ...909...19T",
        "2309.00009",
        "Simulated circumgalactic gas in dwarf halos",
        ["Tanaka, Ren", "Ibsen, Tor"],
        2023,
        "The Astrophysical Journal",
        "ApJ",
        64,
        refs=["2022ApJ...905...15M", "2021MNRAS.906...16D"],
    ),
    _doc(
        10,
        "2026ApJ...910...20B",
        "2601.00010",
        "Surface brightness profiles of Lyman-alpha haloes",
        ["Brandt, Jonas", "Okafor, Adaeze"],
        2026,
        "The Astrophysical Journal",
        "ApJ",
        1,
        refs=["2024ApJ...901...11O", "2023MNRAS.902...12L", "2022ApJ...905...15M"],
    ),
]

BY_BIBCODE = {d["bibcode"]: d for d in CORPUS}
QUERY_LOG: list[dict] = []  # every /search/query call's params (tests inspect it)

RATE_HEADERS = {
    "X-RateLimit-Limit": "5000",
    "X-RateLimit-Remaining": "4987",
    "X-RateLimit-Reset": "1760000000",
}


def _match(q: str) -> list[dict]:
    q = q.strip()
    m = re.match(r'^(references|citations|similar)\(bibcode:"([^"]+)"\)$', q)
    if m:
        kind, bib = m.groups()
        if kind == "references":
            return [
                BY_BIBCODE[b]
                for b in BY_BIBCODE.get(bib, {}).get("references", [])
                if b in BY_BIBCODE
            ]
        if kind == "citations":
            return [d for d in CORPUS if bib in d["references"]]
        return [d for d in CORPUS if d["bibcode"] != bib][:4]
    m = re.match(r"^identifier:\((.*)\)$", q) or re.match(r"^identifier:(.*)$", q)
    if m:
        wanted = {w.strip().strip('"') for w in m.group(1).split(" OR ")}
        return [d for d in CORPUS if wanted & set(d["identifier"])]
    m = re.match(r'^doi:"(.*)"$', q)
    if m:
        return [d for d in CORPUS if m.group(1) in (d.get("doi") or [])]
    if q in ("*", "*:*"):
        return list(CORPUS)
    if q.startswith("SYNTAX"):
        raise ValueError("syntax")
    words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", re.sub(r"\w+:", " ", q))]
    words = [w for w in words if w not in {"and", "the", "abs", "year"}]
    hits = [
        d for d in CORPUS if all(w in (d["title"][0] + " " + d["abstract"]).lower() for w in words)
    ]
    return hits


CITED_BY = {d["bibcode"]: [] for d in CORPUS}
for _d in CORPUS:
    for _r in _d["references"]:
        CITED_BY.setdefault(_r, []).append(_d["bibcode"])


def _public(d: dict) -> dict:
    """What ADS returns: `reference` (cited bibcodes) and `citation` (citing bibcodes)."""
    out = {k: v for k, v in d.items() if k != "references"}
    out["reference"] = list(d["references"])
    out["citation"] = list(CITED_BY.get(d["bibcode"], []))
    return out


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/search/query"):
        params = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        QUERY_LOG.append(params)
        try:
            docs = _match(params.get("q", ""))
        except ValueError:
            return httpx.Response(400, json={"error": {"msg": "syntax error"}})
        sort = params.get("sort", "")
        if sort.startswith("citation_count"):
            docs = sorted(docs, key=lambda d: -d["citation_count"])
        elif sort.startswith("date"):
            docs = sorted(docs, key=lambda d: d["pubdate"], reverse=sort.endswith("desc"))
        start, rows = int(params.get("start", 0)), int(params.get("rows", 10))
        page = [_public(d) for d in docs[start : start + rows]]
        return httpx.Response(
            200, json={"response": {"numFound": len(docs), "docs": page}}, headers=RATE_HEADERS
        )
    if path.endswith("/export/bibtex"):
        codes = json.loads(request.content)["bibcode"]
        entries = []
        for b in codes:
            d = BY_BIBCODE.get(b)
            if d:
                entries.append(
                    f"@ARTICLE{{{b},\n       author = {{{' and '.join(d['author'])}}},\n"
                    f'        title = "{{{d["title"][0]}}}",\n      journal = {{{d["bibstem"][0]}}},\n'
                    f"         year = {d['year']},\n       adsurl = {{https://ui.adsabs.harvard.edu/abs/{b}}}\n}}"
                )
        return httpx.Response(200, json={"export": "\n\n".join(entries)})
    return httpx.Response(404)


def install(monkeypatch=None):
    """Route refsync's ADS client through the fake. Returns an undo function."""
    import types

    from refsync.services import ads as ads_mod

    transport = httpx.MockTransport(handler)

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return httpx.AsyncClient(*args, **kwargs)

    # Patch only the `httpx` name inside refsync.services.ads, not the real
    # module, so other clients (like the test's own ASGI client) are unaffected.
    shim = types.ModuleType("httpx_fake_ads")
    shim.__dict__.update(httpx.__dict__)
    shim.AsyncClient = client

    if monkeypatch is not None:
        monkeypatch.setattr(ads_mod, "httpx", shim)
        monkeypatch.setattr(ads_mod, "get_ads_api_key", lambda: "x" * 20)
        return lambda: None
    orig = (ads_mod.httpx, ads_mod.get_ads_api_key)
    ads_mod.httpx = shim
    ads_mod.get_ads_api_key = lambda: "x" * 20

    def undo():
        ads_mod.httpx, ads_mod.get_ads_api_key = orig

    return undo


# --- PDFs -----------------------------------------------------------------


def make_pdf(title: str, pages: int = 3) -> bytes:
    """A small but real multi-page PDF (no dependencies)."""
    objs = []
    kids = []
    page_objs = []
    for i in range(pages):
        text = f"{title} - page {i + 1}".replace("(", "[").replace(")", "]")
        stream = (
            f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET\n"
            f"BT /F1 11 Tf 72 690 Td (Figure {i + 1}: a placeholder caption for testing.) Tj ET\n"
            "0.2 0.4 0.8 rg 72 420 300 220 re f\n"
        ).encode()
        page_objs.append(stream)
    # 1 catalog, 2 pages, 3 font, then (page, content) pairs
    n_pages = len(page_objs)
    for i in range(n_pages):
        kids.append(f"{4 + 2 * i} 0 R")
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, stream in enumerate(page_objs):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def pdf_handler(request: httpx.Request) -> httpx.Response:
    """arXiv PDFs work; publisher PDFs return a login page (HTML)."""
    url = str(request.url)
    m = re.search(r"arxiv\.org/pdf/(.+)$", url)
    if m:
        return httpx.Response(
            200,
            content=make_pdf(f"arXiv {m.group(1)}"),
            headers={"content-type": "application/pdf"},
        )
    if "PUB_PDF" in url:
        return httpx.Response(
            200, content=b"<html>Please log in</html>", headers={"content-type": "text/html"}
        )
    return httpx.Response(404)
