"""Logical path helpers.

A *logical path* is how a document is addressed everywhere in wiki-mcp:
POSIX separators, NFC-normalised, relative to the knowledge-base root, no
leading/trailing slash (e.g. ``work/项目/20260101-风电项目A/概览.md``).

Backends produce logical paths from their own storage; permission checks
only ever look at logical paths, never at user-supplied filesystem paths.
"""

from __future__ import annotations

import posixpath
import unicodedata


def norm(path: str) -> str:
    """Normalise a path-like string into logical form (does not resolve ``..``)."""
    p = unicodedata.normalize("NFC", path).replace("\\", "/")
    parts = [s for s in p.split("/") if s not in ("", ".")]
    return "/".join(parts)


def segments(path: str) -> tuple[str, ...]:
    """Case-folded segments, used for all prefix comparisons."""
    p = norm(path)
    return tuple(s.casefold() for s in p.split("/")) if p else ()


def has_prefix(path_segs: tuple[str, ...], prefix_segs: tuple[str, ...]) -> bool:
    """Segment-wise prefix test: ``a/bc`` is NOT under ``a/b``."""
    return path_segs[: len(prefix_segs)] == prefix_segs


def join_relative(base_dir: str, rel: str) -> str | None:
    """Resolve ``rel`` against ``base_dir``; None if it escapes the root."""
    rel = unicodedata.normalize("NFC", rel).replace("\\", "/")
    joined = posixpath.normpath(posixpath.join(base_dir, rel)) if base_dir else posixpath.normpath(rel)
    if joined == "." or joined.startswith("../") or joined == ".." or joined.startswith("/"):
        return None
    return norm(joined)
