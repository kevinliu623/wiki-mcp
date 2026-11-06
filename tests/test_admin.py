import pytest
import yaml
from starlette.testclient import TestClient

from wiki_mcp.access import parse_access
from wiki_mcp.admin import build_admin_app, connection_bundle
from wiki_mcp.server import build_components

KEY = "test-admin-key-0123456789abcdef"


@pytest.fixture
def admin(cfg):
    cfg.admin_port = 8766
    comps = build_components(cfg)
    comps.service.rescan_interval = 0
    client = TestClient(build_admin_app(comps, KEY), base_url="http://127.0.0.1:8766")
    client.headers["X-Admin-Key"] = KEY
    return client, comps


def test_requires_key_and_local_host(admin):
    client, _ = admin
    assert client.get("/api/state", headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert client.get("/api/state", headers={"X-Admin-Key": ""}).status_code == 401
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 421
    assert client.get("/api/state").status_code == 200


def test_page_has_strict_headers(admin):
    client, _ = admin
    r = client.get("/", headers={"X-Admin-Key": ""})       # the page itself carries no data
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "script-src 'nonce-" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "__NONCE__" not in r.text


def test_tree_lists_only_indexable_dirs(admin):
    client, _ = admin
    dirs = {d["path"] for d in client.get("/api/tree").json()["dirs"]}
    assert "work/项目/20260101-风电项目A" in dirs and "work/技术文档" in dirs
    assert not any(d.startswith(("personal", "work/日报", "work/wiki")) or "_templates" in d for d in dirs)


def test_create_user_by_directory_takes_effect_immediately(admin):
    client, comps = admin
    r = client.post("/api/users", json={"name": "张三", "dirs": ["work/项目/20260101-风电项目A"]})
    assert r.status_code == 200, r.text
    hd = r.json()["handoff"]
    token = hd["token"]
    assert token.startswith("wm_") and token in hd["config"] and token in hd["command"]
    assert "--scope user" in hd["command"] and "20260101-风电项目A" in hd["prompt"]

    user = comps.access.authenticate(token)
    assert user.name == "张三" and user.scopes == ("风电A",)          # existing single-dir scope reused
    assert comps.service.visible_paths(user.policy) == ["work/项目/20260101-风电项目A/风电项目A概览.md"]

    state = client.get("/api/state").json()
    me = next(u for u in state["users"] if u["name"] == "张三")
    assert me["visible"] == 1
    # file stays valid for the server's own parser
    parse_access(yaml.safe_load(comps.access.path.read_text(encoding="utf-8")))


def test_reuses_existing_single_dir_scope(admin):
    client, comps = admin
    client.post("/api/users", json={"name": "x", "dirs": ["work/项目/20260101-风电项目A"]})
    client.post("/api/users", json={"name": "y", "dirs": ["work/项目/20260101-风电项目A"]})
    names = [s["name"] for s in client.get("/api/state").json()["scopes"]]
    # the fixture already has 风电A -> that dir; it is reused instead of creating a duplicate
    assert names.count("风电A") == 1 and not any(n.startswith("风电项目A") for n in names)


def test_new_dir_scope_named_after_folder_without_date(admin):
    client, comps = admin
    token = client.post("/api/users", json={"name": "q", "dirs": ["work/项目/20260301-保密项目"]}).json()["handoff"]["token"]
    assert comps.access.authenticate(token).scopes == ("保密项目",)
    # a different dir whose name collides gets a suffix
    client.post("/api/scopes", json={"name": "perceptor", "dirs": ["work/代码"]})
    token = client.post("/api/users", json={"name": "r", "dirs": ["work/代码/perceptor"]}).json()["handoff"]["token"]
    assert comps.access.authenticate(token).scopes == ("perceptor-2",)


def test_rejects_excluded_or_unknown_dirs(admin):
    client, _ = admin
    for bad in ["personal", "work/日报", "work/不存在", "../outside", ""]:
        r = client.post("/api/users", json={"name": "z", "dirs": [bad]})
        assert r.status_code == 400, bad
    assert client.post("/api/users", json={"name": "z"}).status_code == 400          # no scope at all
    assert client.post("/api/users", json={"name": "bob", "scopes": ["公共"]}).status_code == 400  # duplicate


def test_update_disable_reset_delete(admin):
    client, comps = admin
    token = client.post("/api/users", json={"name": "u1", "scopes": ["公共"]}).json()["handoff"]["token"]
    assert client.patch("/api/users/u1", json={"scopes": ["公共", "风电A"]}).status_code == 200
    assert "风电A" in comps.access.authenticate(token).scopes

    client.patch("/api/users/u1", json={"disabled": True})
    assert comps.access.authenticate(token) is None
    client.patch("/api/users/u1", json={"disabled": False})
    assert comps.access.authenticate(token).name == "u1"

    new = client.post("/api/users/u1/reset-token").json()["handoff"]["token"]
    assert comps.access.authenticate(token) is None
    assert comps.access.authenticate(new).name == "u1"

    assert client.delete("/api/users/u1").status_code == 200
    assert comps.access.authenticate(new) is None


def test_scope_crud(admin):
    client, comps = admin
    assert client.post("/api/scopes", json={"name": "代码", "dirs": ["work/代码"]}).status_code == 200
    assert client.post("/api/scopes", json={"name": "代码", "dirs": ["work/代码"]}).status_code == 400
    token = client.post("/api/users", json={"name": "u2", "scopes": ["代码"]}).json()["handoff"]["token"]
    # rename propagates to users
    assert client.put("/api/scopes/代码", json={"name": "代码库", "dirs": ["work/代码"]}).status_code == 200
    assert comps.access.authenticate(token).scopes == ("代码库",)
    # cannot delete a scope in use
    assert client.delete("/api/scopes/代码库").status_code == 400
    client.delete("/api/users/u2")
    assert client.delete("/api/scopes/代码库").status_code == 200


def test_preview_uses_same_permissions(admin):
    client, _ = admin
    text = client.get("/api/preview", params={"user": "bob", "search": "保密关键词"}).json()["text"]
    assert "没有找到" in text
    text = client.get("/api/preview", params={"user": "bob"}).json()["text"]
    assert "项目" not in text


def test_log_tail(admin):
    client, comps = admin
    client.post("/api/users", json={"name": "u3", "scopes": ["公共"]})
    events = client.get("/api/log").json()["events"]
    assert events[0]["event"] == "admin" and events[0]["action"] == "create_user"
    assert "token" not in str(events[0]).lower() or "wm_" not in str(events[0])   # no plaintext token in logs


def test_connection_bundle_prompt_lists_dirs():
    b = connection_bundle("http://10.0.0.1:8765/mcp", "wm_x", ["work/项目/20260101-项目A"])
    assert "20260101-项目A" in b["prompt"] and "http://10.0.0.1:8765/mcp" in b["config"]
