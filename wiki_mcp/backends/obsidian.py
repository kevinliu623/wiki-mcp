"""Obsidian vault: Markdown backend + ``[[wikilinks]]`` / ``![[embeds]]`` with Obsidian-style resolution."""

from __future__ import annotations

import posixpath
import re

from ..paths import join_relative, norm
from .base import Doc, DocIndex, Link
from .markdown import CodeSpans, MarkdownBackend

_WIKI = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")


def _wiki_target(inner: str) -> str:
    """``target#heading|alias`` -> ``target``."""
    target = inner.split("|", 1)[0]
    target = re.split(r"[#^]", target, maxsplit=1)[0]
    return target.strip()


class ObsidianBackend(MarkdownBackend):
    name = "obsidian"

    def find_links(self, text: str) -> list[Link]:
        in_code = CodeSpans(text)
        links = [
            Link(m.start(), m.end(), _wiki_target(m.group(2)), embed=bool(m.group(1)), syntax="wiki")
            for m in _WIKI.finditer(text)
            if not in_code(m.start())
        ]
        taken = [(l.start, l.end) for l in links]
        for l in super().find_links(text):
            if not any(s <= l.start < e for s, e in taken):
                links.append(l)
        links.sort(key=lambda l: l.start)
        return links

    def is_doc_target(self, link: Link) -> bool:
        if link.syntax != "wiki":
            return super().is_doc_target(link)
        if not link.target:                  # [[#heading]] = same note
            return False
        ext = posixpath.splitext(link.target)[1].lower()
        # "[[2026.11 计划]]" has a dot but is still a note; only known attachment
        # extensions are treated as non-documents.
        return ext not in _ATTACHMENT_EXTS

    def resolve(self, link: Link, source: Doc | None, index: DocIndex) -> Doc | None:
        if link.syntax != "wiki":
            return super().resolve(link, source, index)
        target = norm(link.target)
        if not target:
            return None
        if target.lower().endswith(".md"):
            target = target[:-3]
        raw = link.target.replace("\\", "/")
        if raw.startswith("./") or raw.startswith("../"):
            if raw.lower().endswith(".md"):
                raw = raw[:-3]
            rel = join_relative(source.dir if source else "", raw)
            return index.get(rel + ".md") if rel else None
        if "/" in target:
            exact = index.get(target + ".md")
            if exact:
                return exact
            suffix = "/" + target.casefold() + ".md"
            candidates = [d for d in index.docs if d.path.casefold().endswith(suffix)]
        else:
            candidates = index.by_stem.get(target.casefold(), [])
        return _pick(candidates, source)


def _pick(candidates: list[Doc], source: Doc | None) -> Doc | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if source is not None:
        same_dir = [d for d in candidates if d.dir == source.dir]
        if same_dir:
            return same_dir[0]
    return min(candidates, key=lambda d: (d.path.count("/"), len(d.path), d.path))


_ATTACHMENT_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".avif",
    ".pdf", ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".webm", ".mov", ".mkv",
    ".canvas", ".base", ".excalidraw", ".docx", ".xlsx", ".pptx", ".zip", ".csv", ".txt",
}
