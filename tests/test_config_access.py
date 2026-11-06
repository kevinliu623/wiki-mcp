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
