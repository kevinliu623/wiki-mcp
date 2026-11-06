"""Backend interface: everything that differs between knowledge-base types.

The core (permissions, budgets, sanitising, audit, tools) only talks to this
interface and only ever handles *logical paths* (see :mod:`wiki_mcp.paths`).
A backend must:

* enumerate documents under its root, pruning anything the exclude matcher
  rejects and never following links out of the root;
* read a document's text and parse its metadata (frontmatter);
* find links in a text and resolve a link target to a document.
"""

from __future__ import annotations

import posixpath
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..paths import norm
from ..policy import ExcludeMatcher


@dataclass(frozen=True)
class Doc:
    path: str          # logical path, e.g. "work/项目/x/概览.md"
    mtime: float
    size: int

    @property
    def title(self) -> str:
        return posixpath.splitext(posixpath.basename(self.path))[0]

    @property
    def dir(self) -> str:
        return posixpath.dirname(self.path)


@dataclass(frozen=True)
class Link:
    start: int         # span in the document text
    end: int
    target: str        # raw target as written (no alias / heading)
    embed: bool        # ![[...]] / ![](...)
    syntax: str        # "wiki" | "md"


class DocIndex:
    """Lookup tables over one scan, used for link resolution."""

    def __init__(self, docs: list[Doc]):
        self.docs = docs
        self.by_path: dict[str, Doc] = {d.path: d for d in docs}
        self.by_fold: dict[str, Doc] = {d.path.casefold(): d for d in docs}
        self.by_stem: dict[str, list[Doc]] = {}
        for d in docs:
            self.by_stem.setdefault(d.title.casefold(), []).append(d)

    def get(self, path: str) -> Doc | None:
        p = norm(path)
        return self.by_path.get(p) or self.by_fold.get(p.casefold())


class Backend(ABC):
    name: str = ""

    def __init__(self, root: Path):
        self.root = root

    @abstractmethod
    def scan(self, exclude: ExcludeMatcher) -> list[Doc]:
        """All documents not rejected by ``exclude``."""

    @abstractmethod
    def read_text(self, doc: Doc) -> str: ...

    @abstractmethod
    def parse_meta(self, text: str) -> dict[str, Any]: ...

    @abstractmethod
    def find_links(self, text: str) -> list[Link]: ...

    @abstractmethod
    def is_doc_target(self, link: Link) -> bool:
        """Whether a link points at a document (vs. a URL or an attachment)."""

    @abstractmethod
    def resolve(self, link: Link, source: Doc | None, index: DocIndex) -> Doc | None:
        """Resolve a document link against all documents (not only visible ones)."""
