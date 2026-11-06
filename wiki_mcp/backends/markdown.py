"""Plain Markdown folder: ``*.md`` files, YAML frontmatter, relative ``[text](path.md)`` links."""

from __future__ import annotations

import bisect
import os
import posixpath
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

from ..paths import join_relative, norm
from ..policy import ExcludeMatcher
from .base import Backend, Doc, DocIndex, Link

# [text](target "title") / ![alt](target); target may be <wrapped in angle brackets>
_MD_LINK = re.compile(r"(!?)\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+\"[^\"\n]*\")?\s*\)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})", re.M)
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")


class CodeSpans:
    """Fenced code blocks and inline code: link syntax inside them is not a link
    (``[[1, 2], [3, 4]]`` in a code sample must survive untouched)."""

    def __init__(self, text: str):
        spans: list[tuple[int, int]] = []
        open_at, fence = -1, ""
        for m in _FENCE.finditer(text):
            f = m.group(1)
            if open_at < 0:
                open_at, fence = m.start(), f
            elif f[0] == fence[0] and len(f) >= len(fence):
                spans.append((open_at, m.end()))
                open_at = -1
        if open_at >= 0:
            spans.append((open_at, len(text)))
        fenced = list(spans)
        for m in _INLINE_CODE.finditer(text):
            if not any(s <= m.start() < e for s, e in fenced):
                spans.append((m.start(), m.end()))
        self.spans = sorted(spans)
        self._starts = [s for s, _ in self.spans]

    def __call__(self, pos: int) -> bool:
        i = bisect.bisect_right(self._starts, pos) - 1
        return i >= 0 and pos < self.spans[i][1]


def _is_junction(path: str) -> bool:
    isj = getattr(os.path, "isjunction", None)
    return bool(isj and isj(path))


class MarkdownBackend(Backend):
    name = "markdown"

    def __init__(self, root: Path):
        super().__init__(root)
        self._real_root = os.path.realpath(root)

    # ---- enumeration -------------------------------------------------
    def scan(self, exclude: ExcludeMatcher) -> list[Doc]:
        docs: list[Doc] = []
        stack: list[tuple[str, str]] = [(str(self.root), "")]
        while stack:
            abs_dir, rel_dir = stack.pop()
            try:
                entries = list(os.scandir(abs_dir))
            except OSError:
                continue
            for e in entries:
                rel = norm(f"{rel_dir}/{e.name}" if rel_dir else e.name)
                if exclude.excluded(rel):
                    continue
                try:
                    # never follow symlinks / junctions: they could point outside the root
                    if e.is_symlink() or _is_junction(e.path):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if self._inside_root(e.path):
                            stack.append((e.path, rel))
                    elif e.is_file(follow_symlinks=False) and e.name.lower().endswith(".md"):
                        st = e.stat(follow_symlinks=False)
                        docs.append(Doc(path=rel, mtime=st.st_mtime, size=st.st_size))
                except OSError:
                    continue
        docs.sort(key=lambda d: d.path)
        return docs

    def _inside_root(self, abs_path: str) -> bool:
        real = os.path.realpath(abs_path)
        try:
            return os.path.commonpath([real, self._real_root]) == self._real_root
        except ValueError:      # different drives on Windows
            return False

    def read_text(self, doc: Doc) -> str:
        abs_path = os.path.join(self.root, *doc.path.split("/"))
        if not self._inside_root(abs_path):
            raise PermissionError(doc.path)
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        return text.lstrip("﻿")

    # ---- metadata ----------------------------------------------------
    def parse_meta(self, text: str) -> dict[str, Any]:
        if not text.startswith("---"):
            return {}
        lines = text.split("\n")
        if lines[0].strip() != "---":
            return {}
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                try:
                    data = yaml.safe_load("\n".join(lines[1:i]))
                except yaml.YAMLError:
                    return {}
                return data if isinstance(data, dict) else {}
        return {}

    # ---- links -------------------------------------------------------
    def find_links(self, text: str) -> list[Link]:
        in_code = CodeSpans(text)
        links = []
        for m in _MD_LINK.finditer(text):
            if in_code(m.start()):
                continue
            target = m.group(2)
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            links.append(Link(m.start(), m.end(), target, embed=bool(m.group(1)), syntax="md"))
        return links

    @staticmethod
    def _md_target_path(target: str) -> str | None:
        """Strip fragment/query and decode; None for URLs and pure anchors."""
        t = target.strip()
        if not t or t.startswith("#") or _SCHEME.match(t) or t.startswith("//"):
            return None
        t = t.split("#", 1)[0].split("?", 1)[0]
        return unquote(t) if t else None

    def is_doc_target(self, link: Link) -> bool:
        if link.syntax != "md":
            return False
        t = self._md_target_path(link.target)
        if t is None:
            return False
        ext = posixpath.splitext(t)[1].lower()
        return ext in ("", ".md")

    def resolve(self, link: Link, source: Doc | None, index: DocIndex) -> Doc | None:
        t = self._md_target_path(link.target)
        if t is None:
            return None
        if t.startswith("/"):
            candidate = norm(t)               # root-relative
        else:
            candidate = join_relative(source.dir if source else "", t)
            if candidate is None:
                return None
        if not candidate.lower().endswith(".md"):
            candidate += ".md"
        return index.get(candidate)
