from __future__ import annotations

from pathlib import Path

from .base import Backend, Doc, DocIndex, Link
from .markdown import MarkdownBackend
from .obsidian import ObsidianBackend

BACKENDS: dict[str, type[Backend]] = {
    MarkdownBackend.name: MarkdownBackend,
    ObsidianBackend.name: ObsidianBackend,
}


def create_backend(name: str, root: Path) -> Backend:
    return BACKENDS[name](root)


__all__ = ["Backend", "Doc", "DocIndex", "Link", "BACKENDS", "create_backend"]
