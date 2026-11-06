"""Permission decisions.

Order (first match wins, deny beats allow):

1. hard exclude  — built-in (hidden ``.xxx`` entries) + config ``exclude``  → deny
2. note frontmatter ``share: false``                                       → deny
3. path under one of the caller's scope prefixes                           → allow
4. everything else                                                         → deny (default-deny)

Exclude patterns:
  * without ``/``  — matches a path *segment* anywhere (``_templates`` hides
    every ``_templates`` folder at any depth), like a gitignore basename;
  * with ``/``     — matches a path prefix from the root (``work/日报``).
Matching is segment-wise and case-insensitive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .paths import has_prefix, norm, segments


class ExcludeMatcher:
    def __init__(self, patterns: Iterable[str]):
        self.names: set[str] = set()
        self.prefixes: list[tuple[str, ...]] = []
        for raw in patterns:
            p = norm(str(raw))
            if not p:
                raise ValueError(f"exclude 模式不能为空或根目录：{raw!r}")
            if "/" in p:
                self.prefixes.append(segments(p))
            else:
                self.names.add(p.casefold())

    def excluded(self, path: str) -> bool:
        segs = segments(path)
        for s in segs:
            if s.startswith(".") or s in self.names:
                return True
        return any(has_prefix(segs, pre) for pre in self.prefixes)


def parse_scope_prefix(raw: str) -> tuple[str, ...]:
    """``"."`` / ``""`` means the whole knowledge base."""
    return segments(raw)


def is_shared(meta: dict[str, Any], share_field: str) -> bool:
    value = meta.get(share_field, True)
    if value is False:
        return False
    if isinstance(value, str) and value.strip().casefold() in ("false", "no", "0", "off"):
        return False
    return True


@dataclass(frozen=True)
class Policy:
    """What one caller may see. Hashable so it can key per-scope caches."""

    prefixes: tuple[tuple[str, ...], ...]

    @classmethod
    def from_prefixes(cls, raw: Iterable[str]) -> "Policy":
        return cls(tuple(sorted({parse_scope_prefix(p) for p in raw})))

    def path_in_scope(self, path: str) -> bool:
        segs = segments(path)
        return any(has_prefix(segs, pre) for pre in self.prefixes)


class Gate:
    """Combines the global exclude list with a caller's policy."""

    def __init__(self, exclude: ExcludeMatcher, share_field: str):
        self.exclude = exclude
        self.share_field = share_field

    def path_allowed(self, policy: Policy, path: str) -> bool:
        return not self.exclude.excluded(path) and policy.path_in_scope(path)

    def doc_allowed(self, policy: Policy, path: str, meta: dict[str, Any]) -> bool:
        return self.path_allowed(policy, path) and is_shared(meta, self.share_field)
