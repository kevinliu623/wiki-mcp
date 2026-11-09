import os
import time

import pytest

from wiki_mcp.access import AccessError, AccessStore, bearer_token, hash_token, parse_access
from wiki_mcp.config import ConfigError, load_config
from tests.conftest import ALICE_TOKEN, BOB_TOKEN


def test_config_rejects_unknown_keys(env):
    (env / "config.yaml").write_text("root: vault\nexclud: [personal]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="exclud"):
        load_config(env / "config.yaml")


def test_config_requires_allowed_hosts_for_wildcard_bind(env):
    (env / "config.yaml").write_text("root: vault\nhost: 0.0.0.0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="allowed_hosts"):
        load_config(env / "config.yaml")


def test_config_paths_relative_to_config_file(cfg, env):
    assert cfg.root == (env / "vault").resolve()
    assert cfg.access_file == (env / "tokens.yaml").resolve()


def test_env_overrides_for_container(env, monkeypatch):
    """Docker: same config.yaml, vault mounted elsewhere, listen on all interfaces."""
    (env / "config.yaml").write_text("root: E:/somewhere/else\nhost: 10.0.0.5\n", encoding="utf-8")
    monkeypatch.setenv("WIKI_MCP_ROOT", str(env / "vault"))
    monkeypatch.setenv("WIKI_MCP_HOST", "0.0.0.0")
    cfg = load_config(env / "config.yaml")
    assert cfg.root == env / "vault"
    assert cfg.host == "0.0.0.0"
    assert cfg.allowed_hosts == ["10.0.0.5"]          # the colleague-facing address stays allowed
    assert cfg.public_url == "http://10.0.0.5:8765/mcp"


def test_env_override_keeps_existing_allowed_hosts(env, monkeypatch):
    (env / "config.yaml").write_text("root: vault\nhost: 10.0.0.5\nallowed_hosts: [wiki.lan, 10.0.0.5]\n",
                                     encoding="utf-8")
    monkeypatch.setenv("WIKI_MCP_HOST", "0.0.0.0")
    cfg = load_config(env / "config.yaml")
    assert cfg.allowed_hosts == ["wiki.lan", "10.0.0.5"]


def test_rescan_interval_config_and_env(env, monkeypatch):
    (env / "config.yaml").write_text("root: vault\nrescan_interval: 5\n", encoding="utf-8")
    assert load_config(env / "config.yaml").rescan_interval == 5.0
    monkeypatch.setenv("WIKI_MCP_RESCAN_INTERVAL", "30")
    assert load_config(env / "config.yaml").rescan_interval == 30.0
    monkeypatch.setenv("WIKI_MCP_RESCAN_INTERVAL", "soon")
    with pytest.raises(ConfigError, match="WIKI_MCP_RESCAN_INTERVAL"):
        load_config(env / "config.yaml")


def test_admin_bind_only_loopback_or_container():
    from wiki_mcp.admin import AdminError, admin_bind
    assert admin_bind({}) == "127.0.0.1"
    assert admin_bind({"WIKI_MCP_ADMIN_BIND": "0.0.0.0"}) == "0.0.0.0"
    with pytest.raises(AdminError):
        admin_bind({"WIKI_MCP_ADMIN_BIND": "10.0.0.5"})


def test_authenticate(cfg):
    store = AccessStore(cfg.access_file)
    assert store.authenticate(ALICE_TOKEN).name == "alice"
    assert store.authenticate(BOB_TOKEN).name == "bob"
    assert store.authenticate("wm_carol") is None           # disabled
    assert store.authenticate("nope") is None
    assert store.authenticate(None) is None


def test_bearer_parsing():
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("bearer  abc ") == "abc"
    assert bearer_token("Basic abc") is None
    assert bearer_token("Bearer ") is None
    assert bearer_token(None) is None


def test_parse_access_validation():
    good = hash_token("x")
    with pytest.raises(AccessError, match="未定义的 scope"):
        parse_access({"scopes": {"a": ["x"]}, "users": [{"name": "u", "token_sha256": good, "scopes": ["b"]}]})
    with pytest.raises(AccessError, match="64 位"):
        parse_access({"scopes": {}, "users": [{"name": "u", "token_sha256": "abc", "scopes": []}]})
    with pytest.raises(AccessError, match="重复"):
        parse_access({"scopes": {}, "users": [
            {"name": "u", "token_sha256": good}, {"name": "v", "token_sha256": good}]})
    with pytest.raises(AccessError, match="未知"):
        parse_access({"scopes": {}, "users": [{"name": "u", "token_sha256": good, "scope": []}]})


def _touch_later(path):
    t = time.time() + 5
    os.utime(path, (t, t))


def test_hot_reload_and_keep_last_valid(cfg):
    store = AccessStore(cfg.access_file)
    original = cfg.access_file.read_text(encoding="utf-8")

    # revoke bob by removing him
    lines = original.replace(f"token_sha256: {hash_token(BOB_TOKEN)}", f"token_sha256: {hash_token('wm_new')}")
    cfg.access_file.write_text(lines, encoding="utf-8")
    _touch_later(cfg.access_file)
    assert store.authenticate(BOB_TOKEN) is None
    assert store.authenticate("wm_new").name == "bob"

    # a broken edit keeps the previous valid table
    cfg.access_file.write_text("users: [ {broken", encoding="utf-8")
    t = time.time() + 10
    os.utime(cfg.access_file, (t, t))
    assert store.authenticate("wm_new").name == "bob"
    assert store.last_error and "重新加载失败" in store.last_error
