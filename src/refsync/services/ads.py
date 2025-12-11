"""
NASA ADS API service for syncing citations.
"""

from datetime import datetime
from typing import Optional

import httpx

from .settings_service import get_ads_api_key

ADS_API_BASE = "https://api.adsabs.harvard.edu/v1"


class ADSError(Exception):
    """Error from ADS API"""

    pass


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

    async def search_by_arxiv_ids(self, arxiv_ids: list[str]) -> dict:
        """
        Search ADS for papers by their arXiv IDs.

        Returns dict mapping arxiv_id -> ADS record (or None if not found)
        """
        if not arxiv_ids:
            return {}

        # Build query: identifier:(arXiv:2301.07041 OR arXiv:2302.12345 OR ...)
        # ADS accepts arXiv IDs in the identifier field
        id_queries = [f"arXiv:{aid}" for aid in arxiv_ids]
        query = f"identifier:({' OR '.join(id_queries)})"

        params = {
            "q": query,
            "fl": "bibcode,doi,pub,volume,page,year,doctype,identifier,title,author",
            "rows": min(len(arxiv_ids), 2000),
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{ADS_API_BASE}/search/query", params=params, headers=self.headers
            )

            if response.status_code == 401:
                raise ADSError("Invalid ADS API key")
            elif response.status_code == 429:
                raise ADSError("ADS rate limit exceeded. Please try again later.")
            elif response.status_code != 200:
                raise ADSError(f"ADS API error: {response.status_code}")

            data = response.json()

        # Map results back to arxiv IDs
        results = {}
        for doc in data.get("response", {}).get("docs", []):
            # Find the arXiv ID in the identifiers
            identifiers = doc.get("identifier", [])
            for ident in identifiers:
                # Identifiers come as "arXiv:2301.07041" or just the ID
                if ident.startswith("arXiv:"):
                    aid = ident.replace("arXiv:", "")
                elif "." in ident and ident.replace(".", "").isdigit():
                    # Looks like an arXiv ID (e.g., "2301.07041")
                    aid = ident
                else:
                    continue

                # Check if this matches one of our requested IDs
                for requested_id in arxiv_ids:
                    # Handle version suffixes (2301.07041v1 -> 2301.07041)
                    base_requested = requested_id.split("v")[0]
                    base_found = aid.split("v")[0]
                    if base_requested == base_found:
                        results[requested_id] = doc
                        break

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

    def _parse_bibtex_entries(
        self, bibtex_str: str, bibcodes: list[str]
    ) -> dict[str, str]:
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

    def is_published(self, ads_record: dict) -> bool:
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


async def sync_papers_with_ads(papers: list, update_callback) -> dict:
    """
    Sync a list of papers with ADS to get updated citation info.

    Args:
        papers: List of Paper objects to sync
        update_callback: Async function(arxiv_id, updates_dict) to save updates

    Returns:
        Dict with sync statistics
    """
    if not papers:
        return {"synced": 0, "published": 0, "errors": 0}

    api_key = get_ads_api_key()
    if not api_key:
        raise ADSError("ADS API key not configured")

    client = ADSClient(api_key)

    # Get arXiv IDs
    arxiv_ids = [p.arxiv_id for p in papers]

    stats = {"synced": 0, "published": 0, "unchanged": 0, "not_found": 0, "errors": 0}

    try:
        # Step 1: Search for all papers in ADS
        ads_records = await client.search_by_arxiv_ids(arxiv_ids)

        # Step 2: Get BibTeX for papers that were found
        bibcodes = [rec["bibcode"] for rec in ads_records.values() if rec]
        bibtex_map = {}
        if bibcodes:
            bibtex_map = await client.get_bibtex(bibcodes)

        # Step 3: Update each paper
        for paper in papers:
            try:
                ads_record = ads_records.get(paper.arxiv_id)

                if not ads_record:
                    stats["not_found"] += 1
                    # Still mark as synced even if not in ADS
                    await update_callback(
                        paper.arxiv_id,
                        {"last_citation_sync": datetime.utcnow().isoformat()},
                    )
                    continue

                bibcode = ads_record.get("bibcode")
                is_pub = client.is_published(ads_record)
                bibtex = bibtex_map.get(bibcode)

                updates = {
                    "ads_bibcode": bibcode,
                    "is_published": is_pub,
                    "last_citation_sync": datetime.utcnow().isoformat(),
                }

                # Add DOI if available
                doi = ads_record.get("doi")
                if doi:
                    if isinstance(doi, list):
                        doi = doi[0]
                    updates["doi"] = doi

                # Add journal ref if published
                if is_pub:
                    pub = ads_record.get("pub", "")
                    vol = ads_record.get("volume", "")
                    page = (
                        ads_record.get("page", [""])[0]
                        if ads_record.get("page")
                        else ""
                    )
                    if pub:
                        journal_ref = pub
                        if vol:
                            journal_ref += f", {vol}"
                        if page:
                            journal_ref += f", {page}"
                        updates["journal_ref"] = journal_ref

                # Update BibTeX if we got one from ADS
                if bibtex:
                    # Replace the cite key with our format (LastName:Year)
                    from .bibtex import update_cite_key_in_bibtex

                    if paper.cite_key:
                        bibtex = update_cite_key_in_bibtex(bibtex, paper.cite_key)
                    updates["bibtex"] = bibtex
                    updates["bibtex_source"] = "ads"

                await update_callback(paper.arxiv_id, updates)

                stats["synced"] += 1
                if is_pub:
                    stats["published"] += 1

            except Exception as e:
                print(f"Error syncing {paper.arxiv_id}: {e}")
                stats["errors"] += 1

    except ADSError:
        raise
    except Exception as e:
        raise ADSError(f"Sync failed: {str(e)}")

    return stats
