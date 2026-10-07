"""
NASA ADS API service for syncing citations.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

from ..models import Paper
from .ads_parse import ads_abstract_url, ads_eprint_pdf_url
from .bibtex import generate_cite_key, update_cite_key_in_bibtex
from .identifiers import make_paper_id
from .settings_service import get_ads_api_key

ADS_API_BASE = "https://api.adsabs.harvard.edu/v1"

# Fields requested when matching library papers against ADS records
_SYNC_FIELDS = "bibcode,alternate_bibcode,doi,pub,volume,page,year,doctype,identifier,title,author"

# Identifiers per ADS query. Lookups go out as GET requests, so this keeps the
# URL a sane length for large libraries.
_QUERY_CHUNK = 50


class ADSError(Exception):
    """Error from ADS API"""

    pass


def _base_arxiv_id(arxiv_id: str) -> str:
    """Strip an 'arXiv:' prefix and version suffix: 'arXiv:2301.07041v2' -> '2301.07041'.

    Uses a trailing-version regex rather than split("v"), which would mangle
    old-style ids whose archive name contains a 'v' (e.g. 'solv-int/9901001').
    """
    aid = arxiv_id.strip()
    if aid.lower().startswith("arxiv:"):
        aid = aid.split(":", 1)[1]
    return re.sub(r"v\d+$", "", aid)


def _chunks(items: list, size: int = _QUERY_CHUNK):
    for i in range(0, len(items), size):
        yield items[i : i + size]


@dataclass
class ADSQueryResult:
    docs: list[dict]
    num_found: int
    rate_limit: dict  # {"limit": int, "remaining": int, "reset": int} (any may be None)


def _rate_limit(headers) -> dict:
    def _int(name):
        try:
            return int(headers.get(name))
        except (TypeError, ValueError):
            return None

    return {
        "limit": _int("X-RateLimit-Limit"),
        "remaining": _int("X-RateLimit-Remaining"),
        "reset": _int("X-RateLimit-Reset"),
    }


def arxiv_id_from_identifiers(identifiers: Optional[list[str]]) -> Optional[str]:
    """
    Pick the bare arXiv id out of an ADS `identifier` list, if there is one.

    ADS lists e.g. ["2023ApJ...950...12P", "arXiv:2301.07041", "10.3847/abc",
    "2023arXiv230107041P"]; this returns "2301.07041". Old-style ids come back
    as "astro-ph/0601234".
    """
    for ident in identifiers or []:
        if ident.lower().startswith("arxiv:"):
            return _base_arxiv_id(ident)
    return None


class ADSClient:
    """Client for NASA ADS API"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_ads_api_key()
        if not self.api_key:
            raise ADSError("ADS API key not configured")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def query(
        self,
        q: str,
        fl: str,
        rows: int = 50,
        start: int = 0,
        sort: Optional[str] = None,
    ) -> "ADSQueryResult":
        """
        Run one /search/query request.

        Accepts full ADS query syntax (abs:, author:"^...", year:, references(),
        citations(), similar(), ...). Returns the docs, the total match count and
        the rate-limit state reported by ADS.
        """
        params = {"q": q, "fl": fl, "rows": min(max(rows, 1), 2000), "start": max(start, 0)}
        if sort:
            params["sort"] = sort

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{ADS_API_BASE}/search/query", params=params, headers=self.headers
            )

        if response.status_code == 401:
            raise ADSError("Invalid ADS API key")
        elif response.status_code == 429:
            raise ADSError("ADS rate limit exceeded. Please try again later.")
        elif response.status_code == 400:
            # Usually a query syntax error; ADS puts the reason in the body
            try:
                detail = response.json().get("error", {}).get("msg") or response.text
            except ValueError:
                detail = response.text
            raise ADSError(f"ADS could not parse the query: {str(detail)[:300]}")
        elif response.status_code != 200:
            raise ADSError(f"ADS API error: {response.status_code}")

        body = response.json().get("response", {})
        return ADSQueryResult(
            docs=body.get("docs", []),
            num_found=int(body.get("numFound", 0)),
            rate_limit=_rate_limit(response.headers),
        )

    async def _search(self, query: str, fl: str, rows: int) -> list[dict]:
        """Run one /search/query request and return its docs."""
        return (await self.query(query, fl, rows=rows)).docs

    async def search_by_arxiv_ids(self, arxiv_ids: list[Optional[str]]) -> dict:
        """
        Search ADS for papers by their arXiv IDs.

        None/empty ids (ADS-only papers) are skipped.
        Returns dict mapping each requested arxiv_id -> ADS record (missing if not found).
        """
        requested = [aid for aid in arxiv_ids if aid]
        if not requested:
            return {}

        # base id -> the requested id strings that map to it (version-insensitive)
        by_base: dict[str, list[str]] = {}
        for aid in requested:
            by_base.setdefault(_base_arxiv_id(aid), []).append(aid)

        results: dict[str, dict] = {}
        for chunk in _chunks(list(by_base)):
            # identifier:("arXiv:2301.07041" OR "arXiv:astro-ph/0601234" OR ...)
            query = "identifier:(" + " OR ".join(f'"arXiv:{b}"' for b in chunk) + ")"
            for doc in await self._search(query, _SYNC_FIELDS, len(chunk) * 2):
                # Identifiers come as "arXiv:2301.07041", bare ids, DOIs, bibcodes...
                for ident in doc.get("identifier", []) or []:
                    base = _base_arxiv_id(ident)
                    if base in by_base:
                        for aid in by_base[base]:
                            results.setdefault(aid, doc)
                        break

        return results

    async def search_by_bibcodes(self, bibcodes: list[Optional[str]]) -> dict:
        """
        Search ADS for papers by bibcode (used for ADS-only papers).

        Matches against each record's canonical bibcode *and* its identifiers /
        alternate bibcodes, so a stored arXiv-style bibcode (e.g.
        '2024arXiv240712345S') still finds the record after ADS re-keys it to the
        journal bibcode on publication.

        Returns dict mapping each requested bibcode -> ADS record (missing if not found).
        """
        requested = list(dict.fromkeys(b for b in bibcodes if b))
        if not requested:
            return {}

        wanted = set(requested)
        results: dict[str, dict] = {}
        for chunk in _chunks(requested):
            query = "identifier:(" + " OR ".join(f'"{b}"' for b in chunk) + ")"
            for doc in await self._search(query, _SYNC_FIELDS, len(chunk) * 2):
                names = {doc.get("bibcode")}
                names.update(doc.get("identifier", []) or [])
                names.update(doc.get("alternate_bibcode", []) or [])
                for b in wanted & names:
                    results.setdefault(b, doc)

        return results

    async def get_bibtex(self, bibcodes: list[str]) -> dict[str, str]:
        """
        Get BibTeX entries for a list of ADS bibcodes.

        Returns dict mapping bibcode -> bibtex string
        """
        if not bibcodes:
            return {}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{ADS_API_BASE}/export/bibtex",
                json={"bibcode": bibcodes},
                headers=self.headers,
            )

            if response.status_code == 401:
                raise ADSError("Invalid ADS API key")
            elif response.status_code == 429:
                raise ADSError("ADS rate limit exceeded. Please try again later.")
            elif response.status_code != 200:
                raise ADSError(f"ADS API error: {response.status_code}")

            data = response.json()

        # Parse the combined bibtex string into individual entries
        bibtex_str = data.get("export", "")
        return self._parse_bibtex_entries(bibtex_str, bibcodes)

    def _parse_bibtex_entries(self, bibtex_str: str, bibcodes: list[str]) -> dict[str, str]:
        """Parse a combined BibTeX string into individual entries by bibcode."""
        results = {}

        # Split on @ARTICLE, @INPROCEEDINGS, etc.
        # Each entry starts with @ and ends before the next @ or end of string
        entries = []
        current_entry = []

        for line in bibtex_str.split("\n"):
            if line.strip().startswith("@") and current_entry:
                entries.append("\n".join(current_entry))
                current_entry = []
            current_entry.append(line)

        if current_entry:
            entries.append("\n".join(current_entry))

        # Match entries to bibcodes
        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue

            # Extract the cite key (first thing after @TYPE{)
            for bibcode in bibcodes:
                # ADS uses bibcode as cite key
                if bibcode in entry:
                    results[bibcode] = entry
                    break

        return results

    @staticmethod
    def is_published(ads_record: dict) -> bool:
        """
        Determine if an ADS record represents a published paper (not just arXiv).
        """
        # Check for journal publication indicators
        pub = ads_record.get("pub", "")
        doi = ads_record.get("doi")
        volume = ads_record.get("volume")
        doctype = ads_record.get("doctype", "")

        # If it has a DOI and volume, it's likely published
        if doi and volume:
            return True

        # Check doctype - "article" usually means published
        if doctype == "article" and pub:
            # Make sure it's not just arXiv
            pub_lower = pub.lower()
            if "arxiv" not in pub_lower and pub_lower not in ["eprint", "e-print"]:
                return True

        # Check if pub field contains a real journal
        if pub:
            pub_lower = pub.lower()
            # Common journal indicators
            if any(
                j in pub_lower
                for j in [
                    "apj",
                    "mnras",
                    "a&a",
                    "nature",
                    "science",
                    "phys. rev",
                    "journal",
                    "monthly notices",
                ]
            ):
                return True

        return False


def _journal_ref(ads_record: dict) -> Optional[str]:
    """Build 'Pub, Vol, Page' from an ADS record, or None if there's no pub."""
    pub = ads_record.get("pub", "")
    if not pub:
        return None
    vol = ads_record.get("volume", "")
    page = (ads_record.get("page") or [""])[0]
    ref = pub
    if vol:
        ref += f", {vol}"
    if page:
        ref += f", {page}"
    return ref


async def sync_papers_with_ads(papers: list, update_callback) -> dict:
    """
    Sync a list of papers with ADS to get updated citation info.

    arXiv-backed papers are matched by arXiv id; ADS-only papers (arxiv_id is
    None) are matched by their stored bibcode.

    Args:
        papers: List of Paper objects to sync
        update_callback: Async function(paper_id, updates_dict) to save updates,
            keyed on the internal `Paper.id`.

    Returns:
        Dict with sync statistics
    """
    if not papers:
        return {"synced": 0, "published": 0, "errors": 0}

    api_key = get_ads_api_key()
    if not api_key:
        raise ADSError("ADS API key not configured")

    client = ADSClient(api_key)

    arxiv_papers = [p for p in papers if p.arxiv_id]
    ads_only_papers = [p for p in papers if not p.arxiv_id and p.bibcode]

    stats = {
        "synced": 0,
        "published": 0,
        "unchanged": 0,
        "not_found": 0,
        # Papers with neither an arXiv id nor a bibcode can't be looked up
        "skipped": len(papers) - len(arxiv_papers) - len(ads_only_papers),
        "errors": 0,
    }

    try:
        # Step 1: Find every paper in ADS
        arxiv_records = await client.search_by_arxiv_ids([p.arxiv_id for p in arxiv_papers])
        bibcode_records = await client.search_by_bibcodes([p.bibcode for p in ads_only_papers])

        matched: list[tuple[Paper, Optional[dict]]] = [
            (p, arxiv_records.get(p.arxiv_id)) for p in arxiv_papers
        ] + [(p, bibcode_records.get(p.bibcode)) for p in ads_only_papers]

        # Step 2: Get BibTeX for papers that were found
        bibcodes = sorted({rec["bibcode"] for _, rec in matched if rec and rec.get("bibcode")})
        bibtex_map = await client.get_bibtex(bibcodes) if bibcodes else {}

        # Step 3: Update each paper (always keyed on the internal id)
        for paper, ads_record in matched:
            try:
                if not ads_record:
                    stats["not_found"] += 1
                    # Still mark as synced even if not in ADS
                    await update_callback(
                        paper.id,
                        {"last_citation_sync": datetime.utcnow().isoformat()},
                    )
                    continue

                bibcode = ads_record.get("bibcode")
                is_pub = client.is_published(ads_record)
                bibtex = bibtex_map.get(bibcode)

                updates = {
                    "bibcode": bibcode,
                    "is_published": is_pub,
                    "last_citation_sync": datetime.utcnow().isoformat(),
                }

                # ADS-only papers: keep the abstract link pointing at the current
                # canonical bibcode (it changes when an eprint gets published)
                if not paper.arxiv_id and bibcode and bibcode != paper.bibcode:
                    updates["ads_url"] = ads_abstract_url(bibcode)

                # Add DOI if available
                doi = ads_record.get("doi")
                if doi:
                    if isinstance(doi, list):
                        doi = doi[0]
                    updates["doi"] = doi

                # Add journal ref if published
                if is_pub:
                    journal_ref = _journal_ref(ads_record)
                    if journal_ref:
                        updates["journal_ref"] = journal_ref

                # Update BibTeX if we got one from ADS
                if bibtex:
                    # Replace the cite key with our format (LastName:Year)
                    if paper.cite_key:
                        bibtex = update_cite_key_in_bibtex(bibtex, paper.cite_key)
                    updates["bibtex"] = bibtex
                    updates["bibtex_source"] = "ads"

                await update_callback(paper.id, updates)

                stats["synced"] += 1
                if is_pub:
                    stats["published"] += 1

            except Exception as e:
                print(f"Error syncing {paper.id}: {e}")
                stats["errors"] += 1

    except ADSError:
        raise
    except Exception as e:
        raise ADSError(f"Sync failed: {str(e)}")

    return stats


def _parse_ads_pubdate(pubdate: Optional[str], year: Optional[int] = None) -> Optional[datetime]:
    """
    Parse an ADS pubdate string into a datetime.

    ADS pubdates look like "2025-05-00" where the day (and sometimes the month)
    can be "00" to mean "unknown". We coerce zeros to 01 so the value is a valid
    date. Falls back to the `year` field (Jan 1) if pubdate is missing/garbage,
    and to None if we have nothing at all.
    """
    if pubdate:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", pubdate)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            mo = mo if 1 <= mo <= 12 else 1
            d = d if 1 <= d <= 28 else 1  # clamp to 28 to avoid month-length issues
            return datetime(y, mo, d)
    if year:
        return datetime(int(year), 1, 1)
    return None


async def fetch_ads_paper(bibcode: str) -> "Paper":
    """
    Fetch paper metadata from ADS by bibcode and build a Paper.

    Used for papers that are only on ADS (never on arXiv, or not yet ingested by
    arXiv). Requires an ADS API key to be configured; raises ADSError otherwise.

    Raises:
        ADSError: if no key is configured, the API errors, or the bibcode is
                  not found.
    """
    client = ADSClient()  # raises ADSError("ADS API key not configured") if unset

    params = {
        "q": f"bibcode:{bibcode}",
        "fl": ("bibcode,title,author,abstract,year,pubdate,pub,volume,page,doi,doctype,identifier"),
        "rows": 1,
    }

    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.get(
            f"{ADS_API_BASE}/search/query", params=params, headers=client.headers
        )
        if response.status_code == 401:
            raise ADSError("Invalid ADS API key")
        elif response.status_code == 429:
            raise ADSError("ADS rate limit exceeded. Please try again later.")
        elif response.status_code != 200:
            raise ADSError(f"ADS API error: {response.status_code}")
        data = response.json()

    docs = data.get("response", {}).get("docs", [])
    if not docs:
        raise ADSError(f"No ADS record found for bibcode: {bibcode}")
    doc = docs[0]
    resolved_bibcode = doc.get("bibcode", bibcode)

    # Fetch BibTeX for this bibcode (reuses the existing export endpoint helper)
    bibtex: Optional[str] = None
    try:
        bibtex_map = await client.get_bibtex([resolved_bibcode])
        bibtex = bibtex_map.get(resolved_bibcode)
    except ADSError:
        # Non-fatal: we can still add the paper without BibTeX; sync can fill later.
        bibtex = None

    # Metadata extraction (ADS returns most fields as lists)
    title = (doc.get("title") or ["Untitled"])[0]
    authors = doc.get("author", []) or []
    abstract = doc.get("abstract")
    published = _parse_ads_pubdate(doc.get("pubdate"), doc.get("year"))

    doi = doc.get("doi")
    if isinstance(doi, list):
        doi = doi[0] if doi else None

    is_pub = client.is_published(doc)

    # Build a journal_ref string if published
    journal_ref = _journal_ref(doc) if is_pub else None

    # If ADS knows an arXiv id for this paper, use it: the internal id then
    # matches what an arXiv-link add would produce, so the same paper can't be
    # added twice via different links, and sync can match it by arXiv id.
    arxiv_id = arxiv_id_from_identifiers(doc.get("identifier"))

    paper = Paper(
        id=make_paper_id(arxiv_id=arxiv_id, bibcode=resolved_bibcode),
        arxiv_id=arxiv_id,
        bibcode=resolved_bibcode,
        source="ads",
        title=title,
        authors=authors,
        abstract=abstract,
        categories=[],
        published=published,
        updated=published,
        pdf_url=(
            f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ads_eprint_pdf_url(resolved_bibcode)
        ),
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
        ads_url=ads_abstract_url(resolved_bibcode),
        added_at=datetime.utcnow(),
        doi=doi,
        journal_ref=journal_ref,
        is_published=is_pub,
    )

    # Cite key + BibTeX (the add-paper route re-checks the key against the library)
    paper.cite_key = generate_cite_key(paper)
    if bibtex:
        if paper.cite_key:
            bibtex = update_cite_key_in_bibtex(bibtex, paper.cite_key)
        paper.bibtex = bibtex
        paper.bibtex_source = "ads"
        paper.last_citation_sync = datetime.utcnow()

    return paper
