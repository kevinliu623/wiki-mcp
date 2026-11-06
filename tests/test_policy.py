from wiki_mcp.paths import join_relative, norm
from wiki_mcp.policy import ExcludeMatcher, Policy, is_shared


def test_norm():
    assert norm("\\work\\项目\\a.md/") == "work/项目/a.md"
    assert norm("./a//b/./c.md") == "a/b/c.md"


def test_join_relative_blocks_escape():
    assert join_relative("work/代码/x", "../../技术文档/a.md") == "work/技术文档/a.md"
    assert join_relative("work", "../../etc/passwd") is None
    assert join_relative("", "../a.md") is None


def test_exclude_segment_and_prefix():
    m = ExcludeMatcher(["personal", "_templates", "work/日报"])
    assert m.excluded("personal/a.md")
    assert m.excluded("work/sub/_templates/t.md")          # name anywhere
    assert m.excluded("work/日报/2026.md")
    assert m.excluded("WORK/日报/x.md")                    # case-insensitive
    assert m.excluded(".obsidian/app.json")                # hidden always
    assert m.excluded("work/.trash/x.md")
    assert not m.excluded("work/日报汇总/x.md")            # segment-wise, not string prefix
    assert not m.excluded("work/项目/personal-notes.md")


def test_scope_prefix_is_segment_wise():
    p = Policy.from_prefixes(["work/项目/2026"])
    assert p.path_in_scope("work/项目/2026/a.md")
    assert not p.path_in_scope("work/项目/20260101-风电项目A/a.md")
    assert Policy.from_prefixes(["."]).path_in_scope("anything/a.md")
    assert not Policy.from_prefixes([]).path_in_scope("a.md")


def test_share_flag():
    assert is_shared({}, "share")
    assert not is_shared({"share": False}, "share")
    assert not is_shared({"share": "no"}, "share")
    assert is_shared({"share": True}, "share")
