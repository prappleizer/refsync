"""
Paper resolution — turn a pasted URL/ID (arXiv or ADS) into a Paper.

This is the single entry point the add-paper route should call. It decides
whether the input is an arXiv reference or an ADS reference and fetches
accordingly, with the arxiv-first rule for ADS links that merely point back at
an arXiv eprint (so we get the richer arXiv metadata + real PDF).
"""

from ..models import Paper
from .ads import fetch_ads_paper
from .ads_parse import bibcode_is_arxiv, parse_ads_bibcode
from .arxiv import ArxivAPIError, fetch_arxiv_paper, parse_arxiv_id


class ResolveError(Exception):
    """Raised when input can't be resolved to a paper via arXiv or ADS."""

    pass


async def resolve_paper(url_or_id: str) -> Paper:
    """
    Resolve a pasted arXiv or ADS URL/identifier into a Paper.

    Order:
      1. If it parses as an arXiv reference -> arXiv fetch (richest metadata).
      2. Else if it parses as an ADS reference:
           a. If the ADS identifier is really an arXiv eprint -> try arXiv fetch
              first; fall back to ADS if arXiv doesn't have it yet.
           b. Otherwise -> ADS fetch by bibcode.
      3. Else -> ResolveError.

    Raises:
        ResolveError: input isn't a recognizable arXiv or ADS reference.
        ArxivAPIError / ADSError: propagated from the underlying fetch so the
            route can surface a meaningful message (e.g. missing ADS key).
    """
    # 1. Direct arXiv reference
    if parse_arxiv_id(url_or_id):
        return await fetch_arxiv_paper(url_or_id)

    # 2. ADS reference
    bibcode = parse_ads_bibcode(url_or_id)
    if bibcode:
        arxiv_id = bibcode_is_arxiv(bibcode)
        if arxiv_id:
            # ADS record is arxiv-only: prefer the richer arXiv fetch, but fall
            # back to ADS if arXiv hasn't got it (the ingest-lag window).
            try:
                return await fetch_arxiv_paper(arxiv_id)
            except ArxivAPIError:
                pass
        return await fetch_ads_paper(bibcode)

    raise ResolveError(f"Could not recognize an arXiv or ADS identifier in: {url_or_id!r}")
