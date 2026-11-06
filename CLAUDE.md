# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

wiki-mcp：把个人 Obsidian / Markdown 知识库封装成**只读**、按 token 授权的 MCP 服务（MCP Python SDK 2.x 的 `MCPServer`，streamable HTTP，`/mcp`）。设计决策与状态（已定 / 提议 / 搁置）记录在 `docs/design.md`，改设计时同步更新它与 README。

v0.1 只有 4 个查询工具：`wiki_search` `wiki_read` `wiki_index` `wiki_recent`。需求投递（收件箱）已搁置，不要顺手加写入能力。

## 命令

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Windows 下用 .venv/Scripts，非 Anaconda base
.venv/Scripts/python -m pytest                                  # 全部测试
.venv/Scripts/python -m pytest tests/test_service.py -k read    # 单个测试
.venv/Scripts/python -m wiki_mcp serve|check|preview|token-new  # 见 wiki_mcp/__main__.py
```

注意：mcp 2.x 中 `FastMCP` 已改名 `MCPServer`（`mcp.server.mcpserver`），v1 的示例代码不能直接用。`server.py` 不能加 `from __future__ import annotations`——SDK 靠真实注解识别 `Context` 参数。

## 架构

请求链路：`BearerAuthMiddleware`（无效 token 直接 401）→ `MCPServer` 工具（再次鉴权 + 限频 + 审计，`to_thread` 调用）→ `WikiService`（`service.py`）→ `Backend`（`backends/`）。

- **逻辑路径**（`paths.py`）：所有权限判断只看后端扫描产出的逻辑路径（POSIX、NFC、相对根目录）。用户输入的 path 只用来在**可见文档集合**里查找，从不拼成文件系统路径打开——这是防路径穿越的根本，新代码要保持。
- **权限**（`policy.py`）：硬排除（隐藏目录 + config `exclude`）→ frontmatter `share: false` → scope 前缀（逐段、不区分大小写）→ 默认拒绝。scope 与用户在 `tokens.yaml`（`access.py`，按 mtime 热加载，改坏保留上一版）；`config.yaml`（`config.py`）是静态的，未知键直接报错。
- **脱敏**（`service.py`）：可见文档中指向不可见/不存在文档的链接替换为 `〔未公开链接〕`/`〔未公开内容〕`；**检索在脱敏后的文本上做**，否则可通过搜索探测受限笔记名。代码块内的 `[[...]]` 不算链接（`CodeSpans`）。"无权"与"不存在"对客户端返回同一文案，只有审计日志区分 `denied`/`not_found`。
- **缓存**：每次调用最多每 `rescan_interval` 秒重扫一次目录；快照版本 = 所有 (path, mtime, size) 的哈希，版本变化时清空按版本缓存的可见集合、脱敏文本、反链图。单篇解析结果按 (mtime, size) 缓存。
- **后端插件**（`backends/base.py`）：`scan / read_text / parse_meta / find_links / is_doc_target / resolve`。`markdown` 处理相对 `[x](a.md)` 链接；`obsidian` 继承它并加 `[[wikilink]]`/`![[embed]]`（按标题或路径后缀解析，同目录优先，再取最短路径）。新类型（Logseq 等）加一个后端，不动核心。

## 测试

`tests/conftest.py` 构造一个含各类受限内容的 fixture vault（personal、日报、模板、wiki 汇总层、`share: false`、跨 scope 链接、代码块）和 alice/bob/owner 三个用户；新增可见性相关功能时，在这里补对应的"越权不可见"断言。`test_server_e2e.py` 起真实 uvicorn + MCP 客户端走 HTTP。

## 不入库的文件

`config.yaml`、`tokens.yaml`、`certs/`、`logs/`、`onboarding-local.*` 已在 .gitignore 中。示例配置只用占位 hash 与示例路径。
