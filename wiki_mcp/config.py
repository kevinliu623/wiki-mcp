"""Server configuration (config.yaml). Static: changes need a restart.

Permissions (scopes / users) live in the separate, hot-reloaded access file
(tokens.yaml) — see :mod:`wiki_mcp.access`.

Unknown keys are rejected: a typo in a security-relevant key (``exclude``)
must fail loudly instead of being silently ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .paths import norm


class ConfigError(ValueError):
    pass


@dataclass
class Limits:
    read_chars: int = 12000          # max body chars per wiki_read call
    search_results: int = 10         # max docs per wiki_search call
    snippets_per_doc: int = 2
    snippet_chars: int = 160
    index_entries: int = 200         # max lines in a wiki_index tree
    recent_results: int = 50
    related_links: int = 20          # max outgoing / backlinks listed by wiki_read
    max_query_chars: int = 200


@dataclass
class Config:
    root: Path
    backend: str = "obsidian"
    host: str = "127.0.0.1"
    port: int = 8765
    allowed_hosts: list[str] = field(default_factory=list)
    tls_certfile: Path | None = None
    tls_keyfile: Path | None = None
    access_file: Path = Path("tokens.yaml")
    log_file: Path = Path("logs/access.jsonl")
    exclude: list[str] = field(default_factory=list)
    frozen: list[str] = field(default_factory=list)
    frozen_note: str = "遗留参考，可能过时"
    status_field: str = "status"
    share_field: str = "share"
    rate_limit_per_minute: int = 60
    admin_port: int = 8766           # local admin page, always bound to 127.0.0.1; 0 disables
    limits: Limits = field(default_factory=Limits)

    @property
    def scheme(self) -> str:
        return "https" if self.tls_certfile else "http"

    @property
    def public_url(self) -> str:
        """The MCP URL colleagues should use."""
        host = self.host
        if host in ("0.0.0.0", "::"):
            host = self.allowed_hosts[0] if self.allowed_hosts else "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{self.scheme}://{host}:{self.port}/mcp"


_TOP_KEYS = {f.name for f in fields(Config)}
_LIMIT_KEYS = {f.name for f in fields(Limits)}


def _check_keys(data: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{where}: 未知配置项 {sorted(unknown)}（允许：{sorted(allowed)}）")


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"找不到配置文件：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: 顶层必须是映射")
    _check_keys(data, _TOP_KEYS, str(path))
    base = path.parent

    def rel(p: Any) -> Path:
        q = Path(str(p))
        return q if q.is_absolute() else (base / q).resolve()

    if "root" not in data:
        raise ConfigError(f"{path}: 缺少 root（知识库根目录）")
    limits_raw = data.pop("limits", None) or {}
    if not isinstance(limits_raw, dict):
        raise ConfigError("limits 必须是映射")
    _check_keys(limits_raw, _LIMIT_KEYS, "limits")
    limits = Limits(**{k: int(v) for k, v in limits_raw.items()})

    cfg = Config(root=rel(data.pop("root")), limits=limits)
    for key in ("tls_certfile", "tls_keyfile", "access_file", "log_file"):
        value = data.pop(key, None)
        if value is not None:
            setattr(cfg, key, rel(value))
    # defaults are relative to the config file's directory too
    cfg.access_file = rel(cfg.access_file)
    cfg.log_file = rel(cfg.log_file)
    for key, value in data.items():
        setattr(cfg, key, value)

    cfg.exclude = [str(x) for x in (cfg.exclude or [])]
    cfg.frozen = [norm(str(x)) for x in (cfg.frozen or [])]
    cfg.allowed_hosts = [str(x) for x in (cfg.allowed_hosts or [])]
    cfg.port = int(cfg.port)
    cfg.rate_limit_per_minute = int(cfg.rate_limit_per_minute)
    cfg.admin_port = int(cfg.admin_port)
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    from .backends import BACKENDS

    if not cfg.root.is_dir():
        raise ConfigError(f"root 不是目录：{cfg.root}")
    if cfg.backend not in BACKENDS:
        raise ConfigError(f"backend 必须是 {sorted(BACKENDS)} 之一，当前：{cfg.backend!r}")
    if bool(cfg.tls_certfile) != bool(cfg.tls_keyfile):
        raise ConfigError("tls_certfile 与 tls_keyfile 必须同时配置")
    if cfg.host in ("0.0.0.0", "::") and not cfg.allowed_hosts:
        raise ConfigError(
            "监听所有网卡（0.0.0.0 / ::）时必须配置 allowed_hosts（同事访问时使用的 IP 或域名），"
            "否则无法做 Host 校验。建议改为监听具体的内网 IP。"
        )
    if cfg.admin_port and (cfg.admin_port == cfg.port or not 0 < cfg.admin_port < 65536):
        raise ConfigError("admin_port 必须是 1–65535 且不同于 port（0 表示不启用管理页面）")
    if cfg.rate_limit_per_minute <= 0:
        raise ConfigError("rate_limit_per_minute 必须为正数")
    for f in fields(Limits):
        if getattr(cfg.limits, f.name) <= 0:
            raise ConfigError(f"limits.{f.name} 必须为正数")
