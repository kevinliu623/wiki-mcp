import os
from pathlib import Path

import pytest

from wiki_mcp.service import HIDDEN_EMBED, HIDDEN_LINK


def visible(svc, user):
    return set(svc.visible_paths(user.policy))


def test_visibility_by_scope(svc, users):
    bob = visible(svc, users["bob"])
    assert bob == {
        "work/技术文档/IoTDB部署.md",
        "work/技术文档/IoTDB运维.md",
        "work/技术文档/代码示例.md",
        "work/代码/perceptor/README.md",
    }
    alice = visible(svc, users["alice"])
    assert "work/项目/20260101-风电项目A/风电项目A概览.md" in alice
    assert "work/项目/20260101-风电项目A/内部报价.md" not in alice          # share: false
    assert not any("保密项目" in p for p in alice)


def test_hard_exclude_beats_full_scope(svc, users):
    owner = visible(svc, users["owner"])
    for p in owner:
        assert not p.startswith(("personal/", "work/日报/", "work/wiki/", ".obsidian"))
        assert "_templates" not in p
    assert "work/项目/20260301-保密项目/机密方案.md" in owner


@pytest.mark.parametrize("query", ["秘密内容", "日报关键词", "模板关键词", "保密关键词", "报价关键词", "汇总关键词"])
def test_search_never_returns_out_of_scope(svc, users, query):
    res = svc.search(users["alice"].policy, query)
    assert res.count == 0


def test_search_cannot_probe_restricted_link_names(svc, users):
    # IoTDB部署 links to [[机密方案]]; bob must not be able to detect that name via search
    res = svc.search(users["bob"].policy, "机密方案")
    assert res.count == 0
    res = svc.search(users["bob"].policy, "风电项目A概览")
    assert res.count == 0


def test_search_hits_and_metadata(svc, users):
    res = svc.search(users["bob"].policy, "公共关键词")
    assert res.count == 2
    assert "work/技术文档/IoTDB部署.md" in res.text
    assert "状态：已冻结" in res.text
    assert "遗留参考" in res.text
    assert "> " in res.text                                   # snippet


def test_search_requires_all_terms(svc, users):
    assert svc.search(users["bob"].policy, "公共关键词 不存在的词").count == 0
    assert svc.search(users["bob"].policy, "   ").outcome == "bad_request"


def test_read_sanitizes_restricted_links(svc, users):
    res = svc.read(users["bob"].policy, "work/技术文档/IoTDB部署.md")
    assert res.outcome == "ok"
    text = res.text
    for leaked in ("风电项目A概览", "机密方案", "别名", "幽灵笔记"):
        assert leaked not in text
    assert HIDDEN_LINK in text and HIDDEN_EMBED in text
    assert "![[架构图.png]]" in text                          # attachments untouched
    assert "[[IoTDB运维]]" in text                            # visible link kept


def test_read_alice_sees_link_to_her_project(svc, users):
    text = svc.read(users["alice"].policy, "work/技术文档/IoTDB部署.md").text
    assert "[[风电项目A概览]]" in text
    assert "机密方案" not in text


def test_markdown_relative_links(svc, users):
    text = svc.read(users["bob"].policy, "work/代码/perceptor/README.md").text
    assert "机密方案" not in text and "方案](" not in text
    assert "[部署](../../技术文档/IoTDB部署.md)" in text
    assert "https://example.com/a.md" in text                 # URLs untouched


def test_read_denied_and_missing_look_identical(svc, users):
    bob = users["bob"].policy
    denied = svc.read(bob, "work/项目/20260101-风电项目A/风电项目A概览.md")
    missing = svc.read(bob, "work/项目/20260101-风电项目A/不存在.md")
    assert denied.outcome == "denied" and missing.outcome == "not_found"
    assert denied.text.replace("风电项目A概览", "不存在") == missing.text
    assert svc.read(bob, "personal/日记.md").outcome in ("not_found", "denied")
    assert "秘密内容" not in svc.read(bob, "../personal/日记.md").text


def test_read_by_title_and_related(svc, users):
    res = svc.read(users["bob"].policy, "IoTDB运维")
    assert res.outcome == "ok"
    assert "# work/技术文档/IoTDB运维.md" in res.text
    assert "出链：work/技术文档/IoTDB部署.md" in res.text
    assert "反链：work/技术文档/IoTDB部署.md" in res.text


def test_backlinks_filtered(svc, users):
    # 风电项目A概览 links to IoTDB部署; bob must not see that backlink
    text = svc.read(users["bob"].policy, "IoTDB部署").text
    assert "风电项目A" not in text
    assert "风电项目A概览" in svc.read(users["alice"].policy, "IoTDB部署").text


def test_read_truncation_and_offset(svc, users, cfg):
    cfg.limits.read_chars = 20
    first = svc.read(users["bob"].policy, "IoTDB运维")
    assert "续读：wiki_read(path=\"work/技术文档/IoTDB运维.md\", offset=20)" in first.text
    second = svc.read(users["bob"].policy, "IoTDB运维", offset=20)
    assert second.outcome == "ok" and "关联笔记" not in second.text
    assert svc.read(users["bob"].policy, "IoTDB运维", offset=10_000).outcome == "bad_request"


def test_index_hides_unscoped_dirs(svc, users):
    root = svc.index(users["bob"].policy).text
    assert "work/" in root
    sub = svc.index(users["bob"].policy, "work").text
    assert "技术文档/ (3)" in sub and "代码/ (1)" in sub
    assert "项目" not in sub and "日报" not in sub and "wiki" not in sub
    denied = svc.index(users["bob"].policy, "work/项目")
    assert denied.outcome == "denied"
    assert svc.index(users["bob"].policy, "work/不存在").outcome == "not_found"


def test_recent(svc, users):
    res = svc.recent(users["alice"].policy, days=7)
    assert res.count == 1
    assert "风电项目A概览.md" in res.text and "状态：进行中" in res.text
    assert svc.recent(users["bob"].policy, days=7).count == 0
    assert svc.recent(users["bob"].policy, days=60).count == 4


def test_live_files_no_sync(svc, users, cfg):
    p = Path(cfg.root) / "work/技术文档/新笔记.md"
    p.write_text("刚写的 新鲜关键词", encoding="utf-8")
    assert svc.search(users["bob"].policy, "新鲜关键词").count == 1
    p.unlink()
    assert svc.search(users["bob"].policy, "新鲜关键词").count == 0


def test_symlink_out_of_root_is_ignored(svc, users, cfg, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "泄漏.md").write_text("越界关键词", encoding="utf-8")
    link = Path(cfg.root) / "work/技术文档/链接"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks on this system")
    assert svc.search(users["owner"].policy, "越界关键词").count == 0


def test_junction_out_of_root_is_ignored(svc, users, cfg, tmp_path):
    _winapi = pytest.importorskip("_winapi")      # Windows only; junctions need no admin rights
    outside = tmp_path / "outside2"
    outside.mkdir()
    (outside / "泄漏.md").write_text("交接点关键词", encoding="utf-8")
    junction = Path(cfg.root) / "work/技术文档/交接点"
    _winapi.CreateJunction(str(outside), str(junction))
    assert junction.is_dir()                       # the junction really is traversable
    assert svc.search(users["owner"].policy, "交接点关键词").count == 0


def test_code_blocks_are_not_links(svc, users):
    text = svc.read(users["bob"].policy, "代码示例").text
    assert 'rows = [["2024-01-01", 10.5], ["x", 1]]' in text
    assert "link = [[代码里的文字]]" in text        # inside a fence: plain text, not a link
    assert "`[[1, 2]]`" in text
    assert "正文 〔未公开链接〕 隐藏" in text


def test_snippet_prefers_body_over_frontmatter(svc, users):
    res = svc.search(users["alice"].policy, "风电项目进度")
    assert res.count == 1
    snippet_lines = [line for line in res.text.splitlines() if line.strip().startswith(">")]
    assert snippet_lines and all("status:" not in line for line in snippet_lines)
