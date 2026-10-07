"""
Adding papers to the library.

Shared by refsync's own add-paper route and by refsync-explore's "send to
refsync", so both apply the same duplicate checks and cite-key rules.
"""

from ..db import PaperRepository
from ..models import Paper
from .bibtex import generate_cite_key, update_cite_key_in_bibtex


class PaperExistsError(Exception):
    """The paper (same internal id) is already in the library."""

    def __init__(self, paper_id: str):
        super().__init__(f"Paper already in library: {paper_id}")
        self.paper_id = paper_id


async def add_paper_to_library(repo: PaperRepository, paper: Paper) -> Paper:
    """
    Insert a fully built Paper into the library.

    Raises PaperExistsError if the paper is already there (same id, arXiv id or
    bibcode; the error carries the existing paper's id). The
    cite key is re-checked against the library's existing keys (the fetchers
    generate one without seeing the library), so a second "Smith:2024" becomes
    "Smith:2024a", and the key inside the stored BibTeX is updated to match.
    """
    if await repo.exists(paper.id):
        raise PaperExistsError(paper.id)
    # Same paper stored under another id (e.g. added from an ADS link before
    # ADS records started using the arXiv id): match on arXiv id / bibcode too.
    existing = await repo.find_existing(arxiv_id=paper.arxiv_id, bibcode=paper.bibcode)
    if existing:
        raise PaperExistsError(existing)

    cite_key = generate_cite_key(paper, await repo.cite_keys())
    if cite_key != paper.cite_key:
        paper.cite_key = cite_key
        if paper.bibtex:
            paper.bibtex = update_cite_key_in_bibtex(paper.bibtex, cite_key)

    return await repo.create(paper)
