"""End-to-end: real uvicorn server, real MCP client over streamable HTTP."""

import json
import socket
import threading
import time

import anyio
import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from wiki_mcp.server import build_app, build_components
from tests.conftest import ALICE_TOKEN, BOB_TOKEN


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(cfg):
    cfg.port = _free_port()
    comps = build_components(cfg)
    comps.service.rescan_interval = 0
    config = uvicorn.Config(build_app(comps), host="127.0.0.1", port=cfg.port, log_level="warning")
    srv = uvicorn.Server(config)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{cfg.port}/mcp", comps
    srv.should_exit = True
    t.join(timeout=5)


def _call(url: str, token: str, tool: str, args: dict) -> tuple[list[str], str]:
    async def go():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                tools = sorted(t.name for t in (await client.list_tools()).tools)
                result = await client.call_tool(tool, args)
                return tools, "".join(getattr(c, "text", "") for c in result.content)
    return anyio.run(go)


def test_rejects_missing_and_bad_token(server):
    url, comps = server
    assert httpx.post(url, json={}).status_code == 401
    r = httpx.post(url, json={}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    events = [json.loads(l) for l in comps.cfg.log_file.read_text(encoding="utf-8").splitlines()]
    assert [e["reason"] for e in events if e["event"] == "auth_failed"] == ["missing_token", "invalid_token"]


def test_rejects_foreign_host_header(server):
    url, _ = server
    r = httpx.post(url, json={}, headers={"Authorization": f"Bearer {BOB_TOKEN}", "Host": "evil.example:80",
                                          "Content-Type": "application/json"})
    assert r.status_code == 421


def test_tools_over_mcp(server):
    url, comps = server
    tools, text = _call(url, BOB_TOKEN, "wiki_search", {"query": "公共关键词"})
    assert tools == ["wiki_index", "wiki_read", "wiki_recent", "wiki_search"]
    assert "IoTDB部署.md" in text

    _, text = _call(url, BOB_TOKEN, "wiki_read", {"path": "work/项目/20260101-风电项目A/风电项目A概览.md"})
    assert text.startswith("未找到或无权访问")
    _, text = _call(url, ALICE_TOKEN, "wiki_read", {"path": "风电项目A概览"})
    assert "风电项目进度" in text

    events = [json.loads(l) for l in comps.cfg.log_file.read_text(encoding="utf-8").splitlines()]
    calls = [e for e in events if e["event"] == "tool_call"]
    assert [(e["user"], e["tool"], e["outcome"]) for e in calls] == [
        ("bob", "wiki_search", "ok"),
        ("bob", "wiki_read", "denied"),
        ("alice", "wiki_read", "ok"),
    ]


def test_rate_limit(server):
    url, comps = server
    comps.limiter.per_minute = 1
    _call(url, BOB_TOKEN, "wiki_recent", {})
    _, text = _call(url, BOB_TOKEN, "wiki_recent", {})
    assert "请求过于频繁" in text
