"""Query service: the four read-only operations, enforced per caller.

Everything a caller gets back passes through two filters:

* **visibility** — only documents the caller's policy allows are listed,
  searched, read or counted;
* **sanitising** — inside a visible document, every link/embed whose target is
  not a visible document (restricted, excluded, ``share: false`` or simply
  missing) is replaced by a neutral marker, so restricted note *names* do not
  leak either. Search runs on the sanitised text, so a query cannot probe for
  restricted names by observing which visible notes match.

Not-found and not-allowed produce the same client-facing message; only the
audit log tells them apart.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .backends import Backend, Doc, DocIndex, Link
from .config import Config
from .paths import has_prefix, norm, segments
from .policy import ExcludeMatcher, Gate, Policy

HIDDEN_LINK = "〔未公开链接〕"
HIDDEN_EMBED = "〔未公开内容〕"
NOT_FOUND = "未找到或无权访问：{path}。可以先用 wiki_search 检索，或用 wiki_index 查看可访问的目录。"


@dataclass
class Result:
    text: str
    outcome: str = "ok"      # ok | not_found | denied | bad_request
    count: int = 0


@dataclass
class _Parsed:
    stamp: tuple[float, int]
    text: str
    meta: dict[str, Any]
    links: list[Link]


class _Snapshot:
    def __init__(self, docs: list[Doc]):
        self.docs = docs
        self.index = DocIndex(docs)
        h = hashlib.sha1()
        for d in docs:
            h.update(f"{d.path}\0{d.mtime}\0{d.size}\n".encode("utf-8"))
        self.version = h.hexdigest()


class WikiService:
    def __init__(
        self,
        cfg: Config,
        backend: Backend,
        *,
        rescan_interval: float | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.cfg = cfg
        self.backend = backend
        self.exclude = ExcludeMatcher(cfg.exclude)
        self.gate = Gate(self.exclude, cfg.share_field)
        self.frozen = [segments(p) for p in cfg.frozen]
        self.rescan_interval = cfg.rescan_interval if rescan_interval is None else rescan_interval
        self.clock = clock
        self._lock = threading.RLock()
        self._snap: _Snapshot | None = None
        self._snap_at = 0.0
        self._parsed: dict[str, _Parsed] = {}
        self._vcache: dict[Any, Any] = {}      # caches valid for one snapshot version

    # ------------------------------------------------------------------ plumbing
    def _snapshot(self) -> _Snapshot:
        now = self.clock()
        if self._snap is not None and now - self._snap_at < self.rescan_interval:
            return self._snap
        snap = _Snapshot(self.backend.scan(self.exclude))
        self._snap_at = now
        if self._snap is None or snap.version != self._snap.version:
            self._snap = snap
            self._vcache = {}
            live = set(snap.index.by_path)
            self._parsed = {k: v for k, v in self._parsed.items() if k in live}
        return self._snap

    def _parse(self, doc: Doc) -> _Parsed:
        stamp = (doc.mtime, doc.size)
        p = self._parsed.get(doc.path)
        if p is None or p.stamp != stamp:
            try:
                text = self.backend.read_text(doc)
            except OSError:
                text = ""
            p = _Parsed(stamp, text, self.backend.parse_meta(text), self.backend.find_links(text))
            self._parsed[doc.path] = p
        return p

    def _cached(self, key: Any, build: Callable[[], Any]) -> Any:
        if key not in self._vcache:
            self._vcache[key] = build()
        return self._vcache[key]

    def _visible(self, policy: Policy, snap: _Snapshot) -> list[Doc]:
        def build() -> list[Doc]:
            return [
                d for d in snap.docs
                if self.gate.path_allowed(policy, d.path)
                and self.gate.doc_allowed(policy, d.path, self._parse(d).meta)
            ]
        return self._cached(("visible", policy), build)

    def _visible_index(self, policy: Policy, snap: _Snapshot) -> DocIndex:
        return self._cached(("vindex", policy), lambda: DocIndex(self._visible(policy, snap)))

    def _resolved(self, doc: Doc, snap: _Snapshot) -> list[tuple[Link, Doc | None, bool]]:
        """(link, resolved doc or None, is_doc_link) for every link in ``doc``."""
        def build():
            out = []
            for link in self._parse(doc).links:
                is_doc = self.backend.is_doc_target(link)
                target = self.backend.resolve(link, doc, snap.index) if is_doc else None
                out.append((link, target, is_doc))
            return out
        return self._cached(("links", doc.path), build)

    def _sanitized(self, doc: Doc, policy: Policy, snap: _Snapshot) -> str:
        def build() -> str:
            text = self._parse(doc).text
            visible = self._visible_index(policy, snap).by_path
            parts, pos = [], 0
            for link, target, is_doc in self._resolved(doc, snap):
                if not is_doc or (target is not None and target.path in visible):
                    continue
                parts.append(text[pos:link.start])
                parts.append(HIDDEN_EMBED if link.embed else HIDDEN_LINK)
                pos = link.end
            parts.append(text[pos:])
            return "".join(parts)
        return self._cached(("sanitized", doc.path, policy), build)

    def _backlinks(self, snap: _Snapshot) -> dict[str, list[str]]:
        def build():
            back: dict[str, list[str]] = {}
            for d in snap.docs:
                seen = set()
                for _, target, _ in self._resolved(d, snap):
                    if target is not None and target.path != d.path and target.path not in seen:
                        seen.add(target.path)
                        back.setdefault(target.path, []).append(d.path)
            return back
        return self._cached(("backlinks",), build)

    def _meta_line(self, doc: Doc) -> str:
        bits = [f"修改：{_fmt_time(doc.mtime)}"]
        status = _scalar(self._parse(doc).meta.get(self.cfg.status_field))
        if status:
            bits.append(f"状态：{status}")
        if any(has_prefix(segments(doc.path), f) for f in self.frozen):
            bits.append(f"⚠ {self.cfg.frozen_note}")
        return " · ".join(bits)

    # ------------------------------------------------------------------ queries
    def search(self, policy: Policy, query: str, limit: int | None = None) -> Result:
        lim = self.cfg.limits
        q = (query or "").strip()[: lim.max_query_chars]
        terms = list(dict.fromkeys(t.lower() for t in q.split()))
        if not terms:
            return Result("请提供检索词（多个词用空格分隔，需全部命中）。", "bad_request")
        n = _clamp(limit or lim.search_results, 1, lim.search_results)
        with self._lock:
            snap = self._snapshot()
            hits = []
            for doc in self._visible(policy, snap):
                text = self._sanitized(doc, policy, snap)
                low, path_low, title_low = text.lower(), doc.path.lower(), doc.title.lower()
                if not all(t in low or t in path_low for t in terms):
                    continue
                score = sum(
                    (10 if t in title_low else 0) + (3 if t in path_low else 0) + min(low.count(t), 20)
                    for t in terms
                )
                hits.append((score, doc, text, low))
            if not hits:
                return Result(f"检索「{q}」：没有找到可访问的匹配笔记。可以换个说法，或用 wiki_index 浏览目录。", "ok", 0)
            hits.sort(key=lambda h: (-h[0], -h[1].mtime, h[1].path))
            out = [f"检索「{q}」：命中 {len(hits)} 篇，显示前 {min(n, len(hits))} 篇（按相关度）。", ""]
            for i, (_, doc, text, low) in enumerate(hits[:n], 1):
                out.append(f"{i}. {doc.path}")
                out.append(f"   {self._meta_line(doc)}")
                for snip in _snippets(text, low, terms, lim.snippets_per_doc, lim.snippet_chars):
                    out.append(f"   > {snip}")
                out.append("")
            out.append("用 wiki_read(path) 阅读全文。")
            return Result("\n".join(out), "ok", len(hits))

    def read(self, policy: Policy, path: str, offset: int = 0) -> Result:
        lim = self.cfg.limits
        p = norm(path or "")
        if not p:
            return Result("请提供笔记路径（来自 wiki_search / wiki_index 的结果，或笔记标题）。", "bad_request")
        with self._lock:
            snap = self._snapshot()
            doc = _lookup(self._visible_index(policy, snap), p)
            if doc is None:
                exists = _lookup(snap.index, p) is not None
                return Result(NOT_FOUND.format(path=path), "denied" if exists else "not_found")
            text = self._sanitized(doc, policy, snap)
            total = len(text)
            offset = max(0, int(offset or 0))
            if offset and offset >= total:
                return Result(f"offset 超出范围：{doc.path} 全文共 {total} 字。", "bad_request")
            end = min(total, offset + lim.read_chars)
            out = [f"# {doc.path}", self._meta_line(doc), "", text[offset:end]]
            if end < total or offset:
                note = f"（第 {offset}–{end} 字 / 共 {total} 字"
                note += f"。续读：wiki_read(path=\"{doc.path}\", offset={end})）" if end < total else "，已到结尾）"
                out += ["", note]
            if offset == 0:
                out += self._related(doc, policy, snap)
            return Result("\n".join(out), "ok", 1)

    def _related(self, doc: Doc, policy: Policy, snap: _Snapshot) -> list[str]:
        visible = self._visible_index(policy, snap).by_path
        outgoing = []
        for _, target, _ in self._resolved(doc, snap):
            if target is not None and target.path in visible and target.path != doc.path and target.path not in outgoing:
                outgoing.append(target.path)
        backward = [p for p in self._backlinks(snap).get(doc.path, []) if p in visible]
        if not outgoing and not backward:
            return []
        cap = self.cfg.limits.related_links
        lines = ["", "---", "关联笔记（可继续 wiki_read）："]
        if outgoing:
            lines.append("出链：" + _cap_list(outgoing, cap))
        if backward:
            lines.append("反链：" + _cap_list(sorted(backward), cap))
        return lines

    def index(self, policy: Policy, path: str = "") -> Result:
        lim = self.cfg.limits
        base = norm(path or "")
        base_segs = segments(base)
        with self._lock:
            snap = self._snapshot()
            docs = [d for d in self._visible(policy, snap) if _under(d.path, base_segs)]
            if not docs:
                if not base:
                    return Result("当前 token 没有任何可访问的笔记。", "ok", 0)
                exists = any(_under(d.path, base_segs) for d in snap.docs)
                return Result(NOT_FOUND.format(path=path), "denied" if exists else "not_found")
            tree: dict[str, dict] = {}
            base_files: list[str] = []
            for d in docs:
                rel = d.path.split("/")[len(base_segs):]
                if len(rel) == 1:
                    base_files.append(rel[0])
                    continue
                node = tree.setdefault(rel[0], {"count": 0, "subdirs": {}, "files": []})
                node["count"] += 1
                if len(rel) == 2:
                    node["files"].append(rel[1])
                else:
                    node["subdirs"][rel[1]] = node["subdirs"].get(rel[1], 0) + 1
            title = base or "（根目录）"
            lines = [f"目录：{title}（可访问笔记 {len(docs)} 篇，括号内为篇数）"]
            truncated = False
            for name in sorted(tree):
                node = tree[name]
                body = [f"- {name}/ ({node['count']})"]
                body += [f"  - {s}/ ({c})" for s, c in sorted(node["subdirs"].items())]
                files = sorted(node["files"])
                body += [f"  - {f}" for f in files[:8]]
                if len(files) > 8:
                    body.append(f"  - …另有 {len(files) - 8} 篇")
                if len(lines) + len(body) > lim.index_entries:
                    truncated = True
                    break
                lines += body
            for f in sorted(base_files):
                if len(lines) >= lim.index_entries:
                    truncated = True
                    break
                lines.append(f"- {f}")
            if truncated:
                lines.append("…（已截断，用 wiki_index(path=子目录) 逐层查看）")
            return Result("\n".join(lines), "ok", len(docs))

    def recent(self, policy: Policy, days: int = 7, limit: int | None = None) -> Result:
        lim = self.cfg.limits
        days = _clamp(int(days or 7), 1, 365)
        n = _clamp(limit or lim.recent_results, 1, lim.recent_results)
        with self._lock:
            snap = self._snapshot()
            cutoff = self.clock() - days * 86400
            docs = sorted(
                (d for d in self._visible(policy, snap) if d.mtime >= cutoff),
                key=lambda d: (-d.mtime, d.path),
            )
            if not docs:
                return Result(f"最近 {days} 天没有可访问的笔记被修改。", "ok", 0)
            lines = [f"最近 {days} 天修改的笔记：共 {len(docs)} 篇，显示前 {min(n, len(docs))} 篇（新→旧）。"]
            for d in docs[:n]:
                lines.append(f"- {d.path}  ·  {self._meta_line(d)}")
            return Result("\n".join(lines), "ok", len(docs))

    # ------------------------------------------------------------------ admin helpers (CLI only)
    def visible_paths(self, policy: Policy) -> list[str]:
        with self._lock:
            snap = self._snapshot()
            return [d.path for d in self._visible(policy, snap)]

    def all_paths(self) -> list[str]:
        with self._lock:
            return [d.path for d in self._snapshot().docs]


# ---------------------------------------------------------------------- helpers
def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _scalar(v: Any) -> str:
    if v is None or isinstance(v, (dict,)):
        return ""
    if isinstance(v, list):
        v = "、".join(str(x) for x in v if not isinstance(x, (dict, list)))
    s = str(v).strip()
    return s[:40]


def _under(path: str, base_segs: tuple[str, ...]) -> bool:
    segs = segments(path)
    return len(segs) > len(base_segs) and has_prefix(segs, base_segs)


def _cap_list(items: list[str], cap: int) -> str:
    shown = "、".join(items[:cap])
    return shown + (f" 等 {len(items)} 篇" if len(items) > cap else "")


def _lookup(index: DocIndex, p: str) -> Doc | None:
    """Exact path, path without .md, then Obsidian-style title / path-suffix match."""
    doc = index.get(p) or index.get(p + ".md")
    if doc:
        return doc
    stem = p[:-3] if p.lower().endswith(".md") else p
    if "/" in stem:
        suffix = "/" + stem.casefold() + ".md"
        cands = [d for d in index.docs if d.path.casefold().endswith(suffix)]
    else:
        cands = index.by_stem.get(stem.casefold(), [])
    if len(cands) == 1:
        return cands[0]
    if cands:
        return min(cands, key=lambda d: (d.path.count("/"), len(d.path), d.path))
    return None


def _body_start(text: str) -> int:
    """Offset just past a leading YAML frontmatter block (0 if none)."""
    if not text.startswith("---"):
        return 0
    end = text.find("\n---", 3)
    if end < 0:
        return 0
    nl = text.find("\n", end + 4)
    return len(text) if nl < 0 else nl + 1


def _snippets(text: str, low: str, terms: list[str], max_n: int, width: int) -> list[str]:
    src = text if len(text) == len(low) else low
    half = max(20, width // 2)
    body = _body_start(low)
    windows: list[list[int]] = []
    for t in terms:                      # first occurrence of each term keeps snippets diverse
        pos, floor = low.find(t, body), body     # prefer the body over frontmatter noise
        if pos < 0:
            pos, floor = low.find(t), 0
        if pos >= 0:
            windows.append([max(floor, pos - half), min(len(low), pos + len(t) + half)])
    windows.sort()
    merged: list[list[int]] = []
    for w in windows:
        if merged and w[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], w[1])
        else:
            merged.append(w)
    out = []
    for s, e in merged[:max_n]:
        frag = " ".join(src[s:e].split())
        out.append(("…" if s > 0 else "") + frag + ("…" if e < len(src) else ""))
    return out
