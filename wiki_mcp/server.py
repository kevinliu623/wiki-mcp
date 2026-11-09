"""MCP server: bearer-token auth, four read-only tools, streamable HTTP.

(No ``from __future__ import annotations`` here: the SDK detects the
``Context`` parameter from real annotations.)
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from anyio import to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from . import __version__
from .access import AccessStore, User, bearer_token
from .audit import AccessLog, RateLimiter, summarize_args
from .backends import create_backend
from .config import Config
from .policy import Policy
from .service import Result, WikiService

logger = logging.getLogger("wiki_mcp")

INSTRUCTIONS = """\
这是一个只读的个人知识库（Markdown 笔记）。你只能看到当前 token 授权的部分。
推荐用法：先 wiki_search 按关键词找到笔记，再 wiki_read 读全文（文末附关联笔记，可顺着继续读）；
wiki_index 浏览目录结构；wiki_recent 查看最近有哪些笔记更新（适合回答"最近进展"类问题）。
每条结果都带最后修改时间和状态；标注"遗留参考"的内容可能已过时，引用时请说明。
"〔未公开链接〕/〔未公开内容〕" 表示链接指向你无权访问或不存在的笔记，无法展开。"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


@dataclass
class Components:
    cfg: Config
    service: WikiService
    access: AccessStore
    log: AccessLog
    limiter: RateLimiter
    mcp: MCPServer


def _client_ip(ctx: Context) -> str | None:
    try:
        client = getattr(ctx.request_context.request, "client", None)
        return client.host if client else None
    except Exception:
        return None


def build_mcp(c: "Components") -> MCPServer:
    mcp = MCPServer(name="wiki-mcp", version=__version__, instructions=INSTRUCTIONS)

    async def call(ctx: Context, tool: str, args: dict[str, Any], fn: Callable[[Policy], Result]) -> str:
        auth = next((v for k, v in (ctx.headers or {}).items() if k.lower() == "authorization"), None)
        user: User | None = c.access.authenticate(bearer_token(auth))
        ip = _client_ip(ctx)
        if user is None:   # the HTTP middleware already rejects these; defence in depth
            c.log.write("tool_call", tool=tool, ip=ip, outcome="unauthenticated")
            return "未授权：缺少或无效的 token。"
        if not c.limiter.allow(user.name):
            c.log.write("tool_call", user=user.name, ip=ip, tool=tool, args=summarize_args(args), outcome="rate_limited")
            return f"请求过于频繁（每分钟上限 {c.cfg.rate_limit_per_minute} 次），请稍后再试。"
        t0 = time.perf_counter()
        try:
            res = await to_thread.run_sync(fn, user.policy)
        except Exception:
            logger.exception("tool %s failed", tool)
            c.log.write("tool_call", user=user.name, ip=ip, tool=tool, args=summarize_args(args), outcome="error")
            return "服务内部错误，已记录。"
        c.log.write(
            "tool_call", user=user.name, ip=ip, tool=tool, args=summarize_args(args),
            outcome=res.outcome, count=res.count, ms=round((time.perf_counter() - t0) * 1000),
        )
        return res.text

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def wiki_search(query: str, ctx: Context, limit: int = 10) -> str:
        """按关键词检索知识库笔记（词法匹配，不区分大小写；多个词用空格分隔，需全部命中）。
        返回笔记路径、最后修改时间/状态和命中片段，不返回全文——需要全文时用 wiki_read。
        limit：最多返回的笔记数（服务端有上限）。"""
        return await call(ctx, "wiki_search", {"query": query, "limit": limit},
                          lambda p: c.service.search(p, query, limit))

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def wiki_read(path: str, ctx: Context, offset: int = 0) -> str:
        """读取一篇笔记的全文。path 可以是 wiki_search / wiki_index 给出的完整路径，也可以只写笔记标题。
        超长笔记会分段返回，按文末提示带 offset 续读。首段末尾附"关联笔记"（出链/反链），可顺着继续读。"""
        return await call(ctx, "wiki_read", {"path": path, "offset": offset},
                          lambda p: c.service.read(p, path, offset))

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def wiki_index(ctx: Context, path: str = "") -> str:
        """浏览知识库目录结构（两级目录 + 各目录笔记数）。不传 path 看根目录；传子目录路径逐层展开。"""
        return await call(ctx, "wiki_index", {"path": path}, lambda p: c.service.index(p, path))

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def wiki_recent(ctx: Context, days: int = 7, limit: int = 30) -> str:
        """列出最近 days 天内修改过的笔记（新→旧，带修改时间与状态）。适合回答"最近有什么进展/更新"。"""
        return await call(ctx, "wiki_recent", {"days": days, "limit": limit},
                          lambda p: c.service.recent(p, days, limit))

    return mcp


class BearerAuthMiddleware:
    """Rejects every HTTP request without a valid bearer token (401)."""

    def __init__(self, app, access: AccessStore, log: AccessLog):
        self.app = app
        self.access = access
        self.log = log
        self._reported_error: str | None = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = bearer_token(Headers(scope=scope).get("authorization"))
        user = self.access.authenticate(token)
        if self.access.last_error and self.access.last_error != self._reported_error:
            self._reported_error = self.access.last_error
            logger.error(self.access.last_error)
            self.log.write("access_reload_failed", error=self.access.last_error)
        if user is None:
            client = scope.get("client")
            self.log.write("auth_failed", ip=client[0] if client else None, path=scope.get("path"),
                           reason="missing_token" if not token else "invalid_token")
            response = JSONResponse(
                {"error": "unauthorized", "message": "缺少或无效的 Bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="wiki-mcp"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def transport_security(cfg: Config) -> TransportSecuritySettings:
    names = []
    for h in [cfg.host, *cfg.allowed_hosts]:
        if h in ("0.0.0.0", "::"):
            continue
        if ":" in h and not h.startswith("["):
            h = f"[{h}]"           # IPv6 literal as it appears in a Host header
        names.append(h)
        if h in ("127.0.0.1", "[::1]"):
            names.append("localhost")
    names = list(dict.fromkeys(names))
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[x for n in names for x in (n, f"{n}:*")],
        allowed_origins=[x for n in names for s in ("http", "https") for x in (f"{s}://{n}", f"{s}://{n}:*")],
    )


def build_components(cfg: Config) -> Components:
    service = WikiService(cfg, create_backend(cfg.backend, cfg.root))
    c = Components(
        cfg=cfg,
        service=service,
        access=AccessStore(cfg.access_file),
        log=AccessLog(cfg.log_file),
        limiter=RateLimiter(cfg.rate_limit_per_minute),
        mcp=None,  # type: ignore[arg-type]
    )
    c.mcp = build_mcp(c)
    return c


def build_app(c: Components):
    inner = c.mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(c.cfg),
        host=c.cfg.host,
    )
    return BearerAuthMiddleware(inner, c.access, c.log)


def serve(cfg: Config, open_admin: bool = False) -> None:
    import uvicorn

    from .admin import admin_key, admin_key_path, admin_url, start_admin_thread

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    c = build_components(cfg)
    app = build_app(c)
    n_docs = len(c.service.all_paths())
    n_users = len(c.access.table().users)
    logger.info("wiki-mcp %s · backend=%s · root=%s · %d 篇可索引笔记 · %d 个有效用户",
                __version__, cfg.backend, cfg.root, n_docs, n_users)
    logger.info("MCP 地址：%s（监听 %s:%d）", cfg.public_url, cfg.host, cfg.port)
    if cfg.scheme == "http" and cfg.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("正在用明文 HTTP 对外提供服务：token 在网络上可被嗅探。建议配置 tls_certfile/tls_keyfile 或置于 HTTPS 反向代理之后。")
    if cfg.admin_port:
        url = admin_url(cfg.admin_port, admin_key(admin_key_path(cfg.access_file)))
        start_admin_thread(c, admin_key(admin_key_path(cfg.access_file)))
        logger.info("管理页面（仅本机可访问）：%s", url)
        if open_admin:
            import webbrowser
            webbrowser.open(url)
    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        ssl_certfile=str(cfg.tls_certfile) if cfg.tls_certfile else None,
        ssl_keyfile=str(cfg.tls_keyfile) if cfg.tls_keyfile else None,
        log_level="info",
        log_config=None,          # use the logging set up above: one format for all console / `docker logs` lines
        access_log=False,         # the audit line (user, tool, outcome) replaces per-request noise
    )
