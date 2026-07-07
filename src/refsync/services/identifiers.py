import hashlib


def make_paper_id(*, arxiv_id: str | None = None, bibcode: str | None = None) -> str:
    """
    Build the internal primary key for a paper.

    Prefers arxiv_id (giving stable, human-readable ids and preserving existing
    URLs after migration). Falls back to a bibcode-derived hash for ADS-only
    papers. Raises if neither is supplied.
    """
    if arxiv_id:
        return "arxiv-" + arxiv_id.replace("/", "-")
    if bibcode:
        digest = hashlib.sha1(bibcode.encode("utf-8")).hexdigest()[:12]
        return "ads-" + digest
    raise ValueError("make_paper_id requires either arxiv_id or bibcode")
