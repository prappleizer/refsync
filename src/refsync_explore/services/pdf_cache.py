"""
Fetch-through PDF cache.

PDFs are downloaded once into explore's pdf dir and served from there, so the
reader is instant when you hop between papers, and PDF.js loads them
same-origin (browsers won't let it fetch arxiv.org directly).

Sources, in order: arXiv (when the paper has an arXiv id), then the ADS link
gateway's EPRINT_PDF, PUB_PDF and ADS_PDF (scans). Publisher PDFs often need
an institutional login; when they come back as an HTML page we report that
instead of caching junk, and the UI offers the links plus a manual upload.
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

from .. import store

USER_AGENT = (
    "refsync-explore/0.1 (personal literature tool; +https://github.com/prappleizer/refsync)"
)
GATEWAY = "https://ui.adsabs.harvard.edu/link_gateway/{bibcode}/{kind}"
MAX_BYTES = 200 * 1024 * 1024


class PdfUnavailable(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _safe_name(paper_id: str) -> str:
    return re.sub(r"[^\w.-]", "_", paper_id) + ".pdf"


def candidate_urls(paper) -> list[tuple[str, str]]:
    """(source label, url) pairs to try for a paper row, best first."""
    out = []
    if paper["arxiv_id"]:
        out.append(("arXiv", f"https://arxiv.org/pdf/{paper['arxiv_id']}"))
    esources = set(json.loads(paper["esources"] or "[]"))
    bib = paper["bibcode"]
    if bib:
        bq = quote(bib, safe="")
        if "EPRINT_PDF" in esources and not paper["arxiv_id"]:
            out.append(("ADS eprint", GATEWAY.format(bibcode=bq, kind="EPRINT_PDF")))
        if "PUB_PDF" in esources:
            out.append(("publisher", GATEWAY.format(bibcode=bq, kind="PUB_PDF")))
        if "ADS_PDF" in esources:
            out.append(("ADS scan", GATEWAY.format(bibcode=bq, kind="ADS_PDF")))
    return out


class PdfCache:
    def __init__(
        self,
        db,
        pdf_dir: Path,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        concurrency: int = 2,
    ):
        self.db = db
        self.pdf_dir = pdf_dir
        self.transport = transport
        self._locks: dict[str, asyncio.Lock] = {}
        self._sem = asyncio.Semaphore(concurrency)
        self._tasks: set[asyncio.Task] = set()

    def path_for(self, paper_id: str) -> Path:
        return self.pdf_dir / _safe_name(paper_id)

    def cached(self, paper_id: str) -> Optional[Path]:
        p = self.path_for(paper_id)
        return p if p.exists() and p.stat().st_size > 0 else None

    async def ensure(self, paper_id: str, retry_failed: bool = True) -> Path:
        """Return the cached PDF path, downloading it first if needed."""
        hit = self.cached(paper_id)
        if hit:
            return hit
        lock = self._locks.setdefault(paper_id, asyncio.Lock())
        async with lock:
            hit = self.cached(paper_id)
            if hit:
                return hit
            paper = await store.get_paper(self.db.conn, paper_id)
            if not paper:
                raise PdfUnavailable("Unknown paper")
            if paper["pdf_status"] == "failed" and not retry_failed:
                raise PdfUnavailable(paper["pdf_error"] or "No PDF available")
            async with self._sem:
                return await self._download(paper)

    async def _download(self, paper) -> Path:
        candidates = candidate_urls(paper)
        if not candidates:
            msg = "ADS lists no PDF for this paper."
            await store.set_pdf_state(self.db.conn, paper["id"], "failed", error=msg)
            raise PdfUnavailable(msg)

        problems = []
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"User-Agent": USER_AGENT},
            transport=self.transport,
        ) as client:
            for source, url in candidates:
                try:
                    resp = await client.get(url)
                except httpx.HTTPError as e:
                    problems.append(f"{source}: {type(e).__name__}")
                    continue
                body = resp.content
                if resp.status_code == 200 and body[:5] == b"%PDF-" and len(body) < MAX_BYTES:
                    dest = self.path_for(paper["id"])
                    tmp = dest.with_suffix(".part")
                    tmp.write_bytes(body)
                    tmp.replace(dest)
                    await store.set_pdf_state(
                        self.db.conn, paper["id"], "ok", path=dest.name, source=source
                    )
                    return dest
                if resp.status_code == 200:
                    problems.append(f"{source}: got a web page, not a PDF (login needed?)")
                else:
                    problems.append(f"{source}: HTTP {resp.status_code}")

        msg = "; ".join(problems)
        await store.set_pdf_state(self.db.conn, paper["id"], "failed", error=msg)
        raise PdfUnavailable(msg)

    async def settled(self, paper_id: str, timeout: float = 20.0) -> Optional[Path]:
        """The cached PDF; if it's downloading right now, wait (up to `timeout`) for it."""
        hit = self.cached(paper_id)
        if hit:
            return hit
        lock = self._locks.get(paper_id)
        if lock is None or not lock.locked():
            return None
        try:
            return await asyncio.wait_for(self.ensure(paper_id, retry_failed=False), timeout)
        except (asyncio.TimeoutError, PdfUnavailable):
            return None

    async def save_upload(self, paper_id: str, data: bytes) -> Path:
        if data[:5] != b"%PDF-":
            raise PdfUnavailable("That file isn't a PDF.")
        dest = self.path_for(paper_id)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        await store.set_pdf_state(self.db.conn, paper_id, "ok", path=dest.name, source="upload")
        return dest

    def prefetch(self, paper_ids: list[str]) -> None:
        """Download in the background (staged papers), so the reader opens instantly."""
        for pid in paper_ids:
            if self.cached(pid):
                continue
            task = asyncio.create_task(self._prefetch_one(pid))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _prefetch_one(self, paper_id: str) -> None:
        try:
            await self.ensure(paper_id, retry_failed=False)
        except PdfUnavailable:
            pass
        except Exception as e:  # never let a background fetch crash the server
            print(f"PDF prefetch failed for {paper_id}: {e}")

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
