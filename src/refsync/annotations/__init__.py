"""
Highlights, notes and figure snips on papers, shared by refsync and refsync-explore.

Annotations belong to a paper (keyed by refsync's paper id), not to a project,
and live in one database (~/.refsync/annotations.db) that both apps read and
write. A note can be tagged with any number of explore projects. Snip images
are PNG files next to the database; the starred snip of a paper is its cover
and is mirrored into refsync's `cover_image` when the paper is in the library.
"""

from .store import AnnotationError, AnnotationStore

__all__ = ["AnnotationStore", "AnnotationError"]
