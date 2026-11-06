from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from wiki_mcp.access import AccessStore, hash_token
from wiki_mcp.backends import create_backend
from wiki_mcp.config import load_config
from wiki_mcp.service import WikiService

ALICE_TOKEN = "wm_alice_test_token"
BOB_TOKEN = "wm_bob_test_token"

VAULT = {
    ".obsidian/app.json": "{}",
    "personal/日记.md": "私人日记 秘密内容",
    "work/日报/2026-11-05.md": "今天的日报 日报关键词",
    "work/_templates/项目模板.md": "模板 模板关键词",
    "work/技术文档/IoTDB部署.md": (
        "---\nstatus: 已冻结\n---\n# IoTDB 部署\n部署步骤 公共关键词。\n"
        "参见 [[风电项目A概览]] 和 [[机密方案|别名]]，嵌入 ![[机密方案]]，"
        "不存在的 [[幽灵笔记]]，图片 ![[架构图.png]]，同目录 [[IoTDB运维]]。\n"
    ),
    "work/技术文档/IoTDB运维.md": "# IoTDB 运维\n运维手册，回链 [[IoTDB部署]]。\n",
    "work/技术文档/代码示例.md": (
        "# 代码示例\n```python\nrows = [[\"2024-01-01\", 10.5], [\"x\", 1]]\nlink = [[代码里的文字]]\n```\n"
        "行内 `[[1, 2]]` 保留，正文 [[机密方案]] 隐藏。\n"
    ),
    "work/代码/perceptor/README.md": (
        "# perceptor\n公共关键词 代码说明。见 [方案](../../项目/20260301-保密项目/机密方案.md) "
        "和 [部署](../../技术文档/IoTDB部署.md)，外链 [官网](https://example.com/a.md)。\n"
    ),
    "work/项目/20260101-风电项目A/风电项目A概览.md": (
        "---\nstatus: 进行中\n---\n# 风电项目A概览\n风电项目进度。依赖 [[IoTDB部署]]，另见 [[机密方案]]。\n"
    ),
    "work/项目/20260101-风电项目A/内部报价.md": "---\nshare: false\n---\n报价 报价关键词 100 万\n",
    "work/项目/20260301-保密项目/机密方案.md": "# 机密方案\n保密关键词\n",
    "work/wiki/concepts/风电.md": "跨项目汇总 汇总关键词 [[风电项目A概览]] [[机密方案]]\n",
}

ACCESS_YAML = f"""
scopes:
  公共: [work/技术文档, work/代码]
  风电A: [work/项目/20260101-风电项目A]
  全部: ["."]
users:
  - name: alice
    token_sha256: {hash_token(ALICE_TOKEN)}
    scopes: [公共, 风电A]
  - name: bob
    token_sha256: {hash_token(BOB_TOKEN)}
    scopes: [公共]
  - name: carol
    token_sha256: {hash_token("wm_carol")}
    scopes: [公共]
    disabled: true
  - name: owner
    token_sha256: {hash_token("wm_owner")}
    scopes: [全部]
"""

CONFIG_YAML = """
backend: obsidian
root: vault
access_file: tokens.yaml
log_file: logs/access.jsonl
exclude: [personal, _templates, work/日报, work/wiki]
frozen: [work/技术文档]
rate_limit_per_minute: 1000
"""


@pytest.fixture
def env(tmp_path: Path):
    vault = tmp_path / "vault"
    old = time.time() - 30 * 86400
    for rel, content in VAULT.items():
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        os.utime(p, (old, old))
    # one recently modified note
    recent = vault / "work/项目/20260101-风电项目A/风电项目A概览.md"
    os.utime(recent, None)
    (tmp_path / "tokens.yaml").write_text(ACCESS_YAML, encoding="utf-8")
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def cfg(env):
    return load_config(env / "config.yaml")


@pytest.fixture
def svc(cfg):
    return WikiService(cfg, create_backend(cfg.backend, cfg.root), rescan_interval=0)


@pytest.fixture
def users(cfg):
    table = AccessStore(cfg.access_file).table()
    return {name: table.user_by_name(name) for name in ("alice", "bob", "owner")}
