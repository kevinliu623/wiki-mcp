"""Local admin page: manage users, scopes and tokens; preview; access log.

Security model (this page can change permissions, so it is the most
sensitive surface of the project):

* always bound to 127.0.0.1, on its own port — colleagues only ever reach the
  read-only MCP listener. The one exception is a container
  (``WIKI_MCP_ADMIN_BIND=0.0.0.0``, set by the Dockerfile): loopback inside a
  container is unreachable from the host, so it binds the container interface
  and the compose file publishes the port on the *host's* 127.0.0.1 only. The
  Host check below still only accepts 127.0.0.1 / localhost;
* every ``/api`` call needs the admin key in the ``X-Admin-Key`` header. A
  custom header cannot be sent cross-origin without a CORS preflight (which we
  never grant), so a malicious web page in the local browser cannot drive the
  API (CSRF). The Host header is checked too (DNS rebinding);
* the key lives in a local file next to tokens.yaml (gitignored) so the link
  stays valid across restarts;
* tokens.yaml is written atomically (temp file + replace), validated with the
  same parser the server uses, then force-reloaded.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .access import AccessError, AccessStore, generate_token, parse_access
from .paths import norm
from .policy import Policy

ADMIN_HOST = "127.0.0.1"
ENV_ADMIN_BIND = "WIKI_MCP_ADMIN_BIND"
_ADMIN_BINDS = ("127.0.0.1", "0.0.0.0")


def admin_bind(environ=None) -> str:
    """Interface the admin server listens on (see module docstring)."""
    value = (os.environ if environ is None else environ).get(ENV_ADMIN_BIND) or ADMIN_HOST
    if value not in _ADMIN_BINDS:
        raise AdminError(f"{ENV_ADMIN_BIND} 只能是 {' 或 '.join(_ADMIN_BINDS)}（容器内用 0.0.0.0），当前：{value!r}")
    return value
_HEADER = (
    "# 访问控制（不入库）。由管理页面维护，也可以手动编辑，保存即生效。\n"
    "# 注意：管理页面保存时会重写本文件，手写的注释不会保留。\n"
)
_DATE_PREFIX = re.compile(r"^\d{6,8}[-_ ]")


class AdminError(ValueError):
    pass


def _clean_name(raw: Any, what: str) -> str:
    name = str(raw or "").strip()
    if not name or len(name) > 40 or any(ord(ch) < 32 for ch in name):
        raise AdminError(f"{what}不能为空、不能超过 40 个字符")
    return name


def admin_key(path: Path) -> str:
    """Load the persistent admin key, creating it on first use."""
    try:
        key = path.read_text(encoding="utf-8").strip()
        if len(key) >= 24:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(24)
    path.write_text(key, encoding="utf-8")
    return key


def admin_key_path(access_file: Path) -> Path:
    return access_file.parent / ".admin_key"


def admin_url(port: int, key: str) -> str:
    return f"http://{ADMIN_HOST}:{port}/#key={key}"


# ---------------------------------------------------------------------- tokens.yaml editing
class AccessEditor:
    def __init__(self, store: AccessStore, dir_ok: Callable[[str], bool]):
        self.store = store
        self.path = store.path
        self.dir_ok = dir_ok           # rejects excluded / nonexistent directories
        self._lock = threading.Lock()

    # raw file <-> dict
    def load(self) -> dict[str, Any]:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise AdminError("tokens.yaml 顶层不是映射，请先手动修复")
        scopes = data.get("scopes") or {}
        data["scopes"] = {str(k): ([v] if isinstance(v, str) else list(v or [])) for k, v in scopes.items()}
        data["users"] = list(data.get("users") or [])
        for u in data["users"]:
            if isinstance(u.get("scopes"), str):
                u["scopes"] = [u["scopes"]]
        return data

    def _save(self, data: dict[str, Any]) -> None:
        clean = {"scopes": data["scopes"], "users": data["users"]}
        try:
            parse_access(clean)                       # same validation as the server
        except AccessError as e:
            raise AdminError(str(e)) from e
        text = _HEADER + yaml.safe_dump(clean, allow_unicode=True, sort_keys=False, default_flow_style=None)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(10):                     # Windows: the target may be briefly open
            try:
                os.replace(tmp, self.path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
        self.store.reload()

    def mutate(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        with self._lock:
            data = self.load()
            result = fn(data)
            self._save(data)
            return result

    # helpers
    @staticmethod
    def _user(data: dict[str, Any], name: str) -> dict[str, Any]:
        for u in data["users"]:
            if u.get("name") == name:
                return u
        raise AdminError(f"找不到用户：{name}")

    def _check_dirs(self, dirs: list[str]) -> list[str]:
        out = []
        for d in dirs:
            p = norm(str(d))
            if not p or not self.dir_ok(p):
                raise AdminError(f"目录不可授权（不存在、已被排除或没有笔记）：{d}")
            out.append(p)
        return list(dict.fromkeys(out))

    def _scope_for_dir(self, data: dict[str, Any], d: str) -> str:
        for name, paths in data["scopes"].items():
            if [norm(p) for p in paths] == [d]:
                return name
        last = d.split("/")[-1]
        base = _DATE_PREFIX.sub("", last) or last
        name, i = base, 2
        while name in data["scopes"]:
            name, i = f"{base}-{i}", i + 1
        data["scopes"][name] = [d]
        return name

    def _resolve_scopes(self, data: dict[str, Any], scopes: list[str], dirs: list[str]) -> list[str]:
        names = [str(s) for s in scopes]
        for s in names:
            if s not in data["scopes"]:
                raise AdminError(f"范围不存在：{s}")
        names += [self._scope_for_dir(data, d) for d in self._check_dirs(dirs)]
        names = list(dict.fromkeys(names))
        if not names:
            raise AdminError("至少选择一个范围或目录")
        return names

    # operations
    def create_user(self, name: str, scopes: list[str], dirs: list[str]) -> str:
        name = _clean_name(name, "名字")

        def op(data):
            if any(u.get("name") == name for u in data["users"]):
                raise AdminError(f"已存在同名用户：{name}")
            token, digest = generate_token()
            data["users"].append({"name": name, "token_sha256": digest,
                                  "scopes": self._resolve_scopes(data, scopes, dirs)})
            return token
        return self.mutate(op)

    def update_user(self, name: str, scopes: list[str] | None, dirs: list[str] | None,
                    disabled: bool | None) -> None:
        def op(data):
            u = self._user(data, name)
            if scopes is not None or dirs is not None:
                u["scopes"] = self._resolve_scopes(data, scopes or [], dirs or [])
            if disabled is not None:
                if disabled:
                    u["disabled"] = True
                else:
                    u.pop("disabled", None)
        self.mutate(op)

    def reset_token(self, name: str) -> str:
        def op(data):
            u = self._user(data, name)
            token, digest = generate_token()
            u["token_sha256"] = digest
            return token
        return self.mutate(op)

    def delete_user(self, name: str) -> None:
        def op(data):
            u = self._user(data, name)
            data["users"].remove(u)
        self.mutate(op)

    def save_scope(self, name: str, dirs: list[str], old_name: str | None = None) -> None:
        name = _clean_name(name, "范围名")

        def op(data):
            paths = self._check_dirs(dirs)
            if not paths:
                raise AdminError("范围至少要包含一个目录")
            if old_name and old_name != name:
                if old_name not in data["scopes"]:
                    raise AdminError(f"范围不存在：{old_name}")
                if name in data["scopes"]:
                    raise AdminError(f"已存在同名范围：{name}")
                data["scopes"] = {(name if k == old_name else k): v for k, v in data["scopes"].items()}
                for u in data["users"]:
                    u["scopes"] = [name if s == old_name else s for s in u.get("scopes") or []]
            elif not old_name and name in data["scopes"]:
                raise AdminError(f"已存在同名范围：{name}")
            data["scopes"][name] = paths
        self.mutate(op)

    def delete_scope(self, name: str) -> None:
        def op(data):
            if name not in data["scopes"]:
                raise AdminError(f"范围不存在：{name}")
            users = [u["name"] for u in data["users"] if name in (u.get("scopes") or [])]
            if users:
                raise AdminError(f"还有用户在使用该范围，请先调整：{'、'.join(users)}")
            del data["scopes"][name]
        self.mutate(op)


# ---------------------------------------------------------------------- handoff text
def connection_bundle(mcp_url: str, token: str, scope_dirs: list[str]) -> dict[str, str]:
    config = json.dumps(
        {"mcpServers": {"wiki": {"type": "http", "url": mcp_url,
                                 "headers": {"Authorization": f"Bearer {token}"}}}},
        ensure_ascii=False, indent=2,
    )
    command = (f'claude mcp add --scope user --transport http wiki {mcp_url} '
               f'--header "Authorization: Bearer {token}"')
    names = "、".join(dict.fromkeys(d.split("/")[-1] for d in scope_dirs)) or "（无）"
    prompt = (
        "## 知识库（MCP: wiki）\n"
        f"- 这是一个只读的团队知识库，你被授权访问：{names}。\n"
        "- 回答与这些内容相关的问题前，先用 wiki_search 检索，再用 wiki_read 读原文；不要凭记忆猜测接口字段、参数等细节。\n"
        "- 引用时注明笔记路径和修改时间/状态；状态显示仍在进行中（设计、联调等）的内容，要提醒结论可能变化。\n"
        "- 查不到就直接说知识库里没有，不要编造。\n"
        "- 〔未公开链接〕表示相关内容不在授权范围内，不要尝试绕过。\n"
    )
    message = (
        "知识库 MCP 接入方式（token 请勿提交进代码仓库）：\n\n"
        f"1) Claude Code：\n{command}\n\n"
        f"2) 其他 MCP 客户端（Cursor 等）配置：\n{config}\n\n"
        f"3) 建议加到 CLAUDE.md / 规则里的提示词：\n{prompt}"
    )
    return {"token": token, "config": config, "command": command, "prompt": prompt, "message": message}


# ---------------------------------------------------------------------- HTTP app
def _tail_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 1_000_000))
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in chunk.splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out


def build_admin_app(c, key: str) -> Starlette:
    """``c`` is a :class:`wiki_mcp.server.Components`."""
    page = (Path(__file__).with_name("admin_page.html")).read_text(encoding="utf-8")
    allowed_hosts = {f"{ADMIN_HOST}:{c.cfg.admin_port}", f"localhost:{c.cfg.admin_port}", ADMIN_HOST, "localhost"}

    def dir_counts() -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in c.service.all_paths():
            parts = p.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i])
                counts[d] = counts.get(d, 0) + 1
        return counts

    editor = AccessEditor(c.access, lambda d: d in dir_counts())

    def scope_dirs(data, names) -> list[str]:
        return [norm(p) for s in names for p in data["scopes"].get(s, [])]

    def visible_count(data, names) -> int:
        return len(c.service.visible_paths(Policy.from_prefixes(scope_dirs(data, names))))

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            raise AdminError("请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise AdminError("请求体必须是对象")
        return data

    def ok(payload: Any = None) -> JSONResponse:
        return JSONResponse({"ok": True, **(payload or {})})

    # -- routes
    async def index(request: Request) -> Response:
        nonce = secrets.token_urlsafe(16)
        html = page.replace("__NONCE__", nonce)
        return HTMLResponse(html, headers={
            "Content-Security-Policy": (f"default-src 'none'; script-src 'nonce-{nonce}'; "
                                        f"style-src 'nonce-{nonce}'; connect-src 'self'; img-src data:; "
                                        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Cache-Control": "no-store",
        })

    async def state(request: Request) -> Response:
        data = editor.load()
        users = []
        for u in data["users"]:
            names = list(u.get("scopes") or [])
            users.append({
                "name": u.get("name"), "scopes": names, "disabled": bool(u.get("disabled")),
                "visible": visible_count(data, names),
            })
        scopes = [{"name": n, "dirs": [norm(p) for p in ps],
                   "users": [u.get("name") for u in data["users"] if n in (u.get("scopes") or [])],
                   "visible": len(c.service.visible_paths(Policy.from_prefixes(ps)))}
                  for n, ps in data["scopes"].items()]
        return ok({"users": users, "scopes": scopes, "mcp_url": c.cfg.public_url,
                   "plain_http": c.cfg.scheme == "http" and c.cfg.host not in ("127.0.0.1", "localhost", "::1"),
                   "error": c.access.last_error, "root": str(c.cfg.root)})

    async def tree(request: Request) -> Response:
        return ok({"dirs": [{"path": d, "count": n} for d, n in sorted(dir_counts().items())]})

    def handoff(name: str, token: str) -> dict[str, str]:
        data = editor.load()
        u = editor._user(data, name)
        return connection_bundle(c.cfg.public_url, token, scope_dirs(data, u.get("scopes") or []))

    async def create_user(request: Request) -> Response:
        b = await body(request)
        token = editor.create_user(b.get("name"), list(b.get("scopes") or []), list(b.get("dirs") or []))
        name = _clean_name(b.get("name"), "名字")
        c.log.write("admin", action="create_user", target=name)
        return ok({"handoff": handoff(name, token)})

    async def update_user(request: Request) -> Response:
        name = request.path_params["name"]
        b = await body(request)
        editor.update_user(
            name,
            list(b["scopes"]) if "scopes" in b else None,
            list(b["dirs"]) if "dirs" in b else None,
            bool(b["disabled"]) if "disabled" in b else None,
        )
        c.log.write("admin", action="update_user", target=name, detail=b)
        return ok()

    async def reset_token(request: Request) -> Response:
        name = request.path_params["name"]
        token = editor.reset_token(name)
        c.log.write("admin", action="reset_token", target=name)
        return ok({"handoff": handoff(name, token)})

    async def delete_user(request: Request) -> Response:
        name = request.path_params["name"]
        editor.delete_user(name)
        c.log.write("admin", action="delete_user", target=name)
        return ok()

    async def save_scope(request: Request) -> Response:
        b = await body(request)
        old = request.path_params.get("name")
        editor.save_scope(b.get("name") or old, list(b.get("dirs") or []), old_name=old)
        c.log.write("admin", action="save_scope", target=b.get("name") or old, detail=b)
        return ok()

    async def delete_scope(request: Request) -> Response:
        name = request.path_params["name"]
        editor.delete_scope(name)
        c.log.write("admin", action="delete_scope", target=name)
        return ok()

    async def preview(request: Request) -> Response:
        q = request.query_params
        data = editor.load()
        u = editor._user(data, q.get("user", ""))
        policy = Policy.from_prefixes(scope_dirs(data, u.get("scopes") or []))
        if q.get("search"):
            res = c.service.search(policy, q["search"])
        elif q.get("read"):
            res = c.service.read(policy, q["read"])
        else:
            res = c.service.index(policy, q.get("path", ""))
        return ok({"text": res.text})

    async def log(request: Request) -> Response:
        try:
            limit = max(1, min(1000, int(request.query_params.get("limit", "300"))))
        except ValueError:
            raise AdminError("limit 必须是整数")
        return ok({"events": _tail_jsonl(c.cfg.log_file, limit)})

    routes = [
        Route("/", index),
        Route("/api/state", state),
        Route("/api/tree", tree),
        Route("/api/users", create_user, methods=["POST"]),
        Route("/api/users/{name}", update_user, methods=["PATCH"]),
        Route("/api/users/{name}", delete_user, methods=["DELETE"]),
        Route("/api/users/{name}/reset-token", reset_token, methods=["POST"]),
        Route("/api/scopes", save_scope, methods=["POST"]),
        Route("/api/scopes/{name}", save_scope, methods=["PUT"]),
        Route("/api/scopes/{name}", delete_scope, methods=["DELETE"]),
        Route("/api/preview", preview),
        Route("/api/log", log),
    ]
    async def on_admin_error(request: Request, exc: Exception) -> Response:
        return JSONResponse({"error": str(exc)}, status_code=400)

    app = Starlette(routes=routes, exception_handlers={AdminError: on_admin_error})

    async def guard(scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            if headers.get("host", "") not in allowed_hosts:
                await JSONResponse({"error": "invalid host"}, status_code=421)(scope, receive, send)
                return
            if scope["path"].startswith("/api/"):
                if not secrets.compare_digest(headers.get("x-admin-key", ""), key):
                    await JSONResponse({"error": "管理口令无效，请使用启动时打印的管理页面链接"},
                                       status_code=401)(scope, receive, send)
                    return
        await app(scope, receive, send)

    return guard


def start_admin_thread(c, key: str) -> threading.Thread:
    """Run the admin page in a daemon thread (the MCP server owns the main thread)."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(build_admin_app(c, key), host=admin_bind(),
                                           port=c.cfg.admin_port, log_level="warning",
                                           log_config=None, access_log=False))
    t = threading.Thread(target=server.run, name="wiki-mcp-admin", daemon=True)
    t.start()
    return t
