"""Access file (tokens.yaml): scopes + users, hot-reloaded.

Format::

    scopes:
      技术公共: [work/技术文档, work/代码]
      风电项目A: [work/项目/20260101-风电项目A]
    users:
      - name: 张三
        token_sha256: <64 hex>      # only the hash is stored
        scopes: [技术公共, 风电项目A]
      - name: 李四
        token_sha256: <64 hex>
        scopes: [技术公共]
        disabled: true              # revoke without deleting

The file is re-read whenever its mtime/size changes, so adding people,
changing scopes and revoking take effect on the next request. If an edit
leaves the file invalid, the last valid version stays in force and the
error is reported through ``last_error``.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .policy import Policy

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AccessError(ValueError):
    pass


@dataclass(frozen=True)
class User:
    name: str
    scopes: tuple[str, ...]
    policy: Policy


@dataclass(frozen=True)
class AccessTable:
    scopes: dict[str, tuple[str, ...]]
    users: dict[str, User]          # token_sha256 -> User
    disabled: frozenset[str]        # names of disabled users

    def user_by_name(self, name: str) -> User | None:
        for u in self.users.values():
            if u.name == name:
                return u
        return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str]:
    token = "wm_" + secrets.token_urlsafe(32)
    return token, hash_token(token)


def parse_access(data: Any) -> AccessTable:
    if not isinstance(data, dict):
        raise AccessError("顶层必须是映射，包含 scopes 与 users")
    unknown = set(data) - {"scopes", "users"}
    if unknown:
        raise AccessError(f"未知顶层键：{sorted(unknown)}")

    raw_scopes = data.get("scopes") or {}
    if not isinstance(raw_scopes, dict):
        raise AccessError("scopes 必须是映射：scope名 -> 目录列表")
    scopes: dict[str, tuple[str, ...]] = {}
    for name, paths in raw_scopes.items():
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not paths:
            raise AccessError(f"scope {name!r} 必须是非空目录列表")
        scopes[str(name)] = tuple(str(p) for p in paths)

    raw_users = data.get("users") or []
    if not isinstance(raw_users, list):
        raise AccessError("users 必须是列表")
    users: dict[str, User] = {}
    names: set[str] = set()
    disabled: set[str] = set()
    for i, entry in enumerate(raw_users):
        if not isinstance(entry, dict):
            raise AccessError(f"users[{i}] 必须是映射")
        unknown = set(entry) - {"name", "token_sha256", "scopes", "disabled"}
        if unknown:
            raise AccessError(f"users[{i}] 未知键：{sorted(unknown)}")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise AccessError(f"users[{i}] 缺少 name")
        if name in names:
            raise AccessError(f"用户名重复：{name}")
        names.add(name)
        digest = str(entry.get("token_sha256") or "").strip().lower()
        if not _HEX64.match(digest):
            raise AccessError(f"用户 {name}: token_sha256 必须是 64 位十六进制（用 `wiki-mcp token-new` 生成）")
        if digest in users:
            raise AccessError(f"用户 {name}: token 与 {users[digest].name} 重复")
        user_scopes = entry.get("scopes") or []
        if isinstance(user_scopes, str):
            user_scopes = [user_scopes]
        for s in user_scopes:
            if str(s) not in scopes:
                raise AccessError(f"用户 {name}: 引用了未定义的 scope {s!r}")
        if entry.get("disabled") is True:
            disabled.add(name)
            continue
        prefixes = [p for s in user_scopes for p in scopes[str(s)]]
        users[digest] = User(name=name, scopes=tuple(str(s) for s in user_scopes), policy=Policy.from_prefixes(prefixes))
    return AccessTable(scopes=scopes, users=users, disabled=frozenset(disabled))


def load_access(path: Path) -> AccessTable:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise AccessError(f"YAML 解析失败：{e}") from e
    return parse_access(data)


class AccessStore:
    """Thread-safe, hot-reloading view of the access file."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._stamp: tuple[int, int] | None = None
        self.last_error: str | None = None
        self._table = load_access(path)   # first load must succeed
        self._stamp = self._current_stamp()

    def _current_stamp(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def table(self) -> AccessTable:
        stamp = self._current_stamp()
        if stamp == self._stamp:
            return self._table
        with self._lock:
            if stamp != self._stamp:
                self._stamp = stamp
                try:
                    self._table = load_access(self.path)
                    self.last_error = None
                except (AccessError, OSError) as e:
                    # keep the last valid table in force
                    self.last_error = f"{self.path} 重新加载失败，继续使用上一版有效配置：{e}"
            return self._table

    def authenticate(self, token: str | None) -> User | None:
        if not token:
            return None
        return self.table().users.get(hash_token(token))


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    value = value.strip()
    return value or None
