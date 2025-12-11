from abc import ABC, abstractmethod
from typing import Optional

from ..models import (
    Paper,
    PaperUpdate,
    SearchQuery,
    SearchResult,
    Shelf,
    ShelfCreate,
    ShelfUpdate,
    Tag,
    TagCreate,
)


class PaperRepository(ABC):
    """Abstract interface for paper storage"""

    @abstractmethod
    async def create(self, paper: Paper) -> Paper:
        """Add a new paper to the library"""
        pass

    @abstractmethod
    async def get(self, arxiv_id: str) -> Optional[Paper]:
        """Get a paper by arXiv ID"""
        pass

    @abstractmethod
    async def update(self, arxiv_id: str, data: PaperUpdate) -> Optional[Paper]:
        """Update paper metadata"""
        pass

    @abstractmethod
    async def delete(self, arxiv_id: str) -> bool:
        """Remove a paper from the library"""
        pass

    @abstractmethod
    async def list_all(self, limit: int = 50, offset: int = 0) -> list[Paper]:
        """List all papers with pagination"""
        pass

    @abstractmethod
    async def search(self, query: SearchQuery) -> SearchResult:
        """Search papers with filters"""
        pass

    @abstractmethod
    async def exists(self, arxiv_id: str) -> bool:
        """Check if a paper exists in the library"""
        pass

    @abstractmethod
    async def set_cover(self, arxiv_id: str, cover_path: str) -> Optional[Paper]:
        """Set cover image path for a paper"""
        pass


class ShelfRepository(ABC):
    """Abstract interface for shelf storage"""

    @abstractmethod
    async def create(self, shelf: ShelfCreate) -> Shelf:
        """Create a new shelf"""
        pass

    @abstractmethod
    async def get(self, shelf_id: str) -> Optional[Shelf]:
        """Get a shelf by ID"""
        pass

    @abstractmethod
    async def get_by_name(self, name: str) -> Optional[Shelf]:
        """Get a shelf by name"""
        pass

    @abstractmethod
    async def update(self, shelf_id: str, data: ShelfUpdate) -> Optional[Shelf]:
        """Update shelf metadata"""
        pass

    @abstractmethod
    async def delete(self, shelf_id: str) -> bool:
        """Delete a shelf"""
        pass

    @abstractmethod
    async def list_all(self) -> list[Shelf]:
        """List all shelves"""
        pass


class TagRepository(ABC):
    """Abstract interface for tag storage"""

    @abstractmethod
    async def create(self, tag: TagCreate) -> Tag:
        """Create a new tag"""
        pass

    @abstractmethod
    async def get(self, name: str) -> Optional[Tag]:
        """Get a tag by name"""
        pass

    @abstractmethod
    async def delete(self, name: str) -> bool:
        """Delete a tag"""
        pass

    @abstractmethod
    async def list_all(self) -> list[Tag]:
        """List all tags"""
        pass

    @abstractmethod
    async def update_color(self, name: str, color: str) -> Optional[Tag]:
        """Update tag color"""
        pass
