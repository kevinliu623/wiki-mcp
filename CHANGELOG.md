# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
版本号在 `pyproject.toml` 与 `wiki_mcp/__init__.py` 两处维护；发布时打 `vX.Y.Z` 标签。

## [Unreleased]

## [0.2.0] - 2026-11-10

Docker 部署。

### 新增
- 镜像发布到 `ghcr.io/kevinliu623/wiki-mcp`（`0.2.0`、`latest`）
- Docker 部署：`Dockerfile` + `compose.yaml`（开机自启、健康检查、日志轮转）；与本机运行共用 `config.yaml` 与 `data/`
- 环境变量覆盖：`WIKI_MCP_ROOT`、`WIKI_MCP_HOST`（覆盖后原 `host` 自动加入 `allowed_hosts`）、`WIKI_MCP_RESCAN_INTERVAL`、`WIKI_MCP_ADMIN_BIND`（仅允许 `127.0.0.1` / 容器内 `0.0.0.0`）
- 配置项 `rescan_interval`（目录重扫间隔，默认 2 秒）
- 访问日志同时打印到控制台（`docker compose logs` 可见）

### 变更
- 运行时状态建议统一放在 `data/`（`access_file: data/tokens.yaml`、`log_file: data/logs/access.jsonl`）；旧配置写法仍然有效
- 启动日志显示同事访问地址（而非监听地址）；关闭 uvicorn 逐请求日志，由访问日志代替

## [0.1.0] - 2026-11-06

首个可用版本：只读、按 token 授权的知识库 MCP 服务。

### 新增
- 4 个只读 MCP 工具：`wiki_search`、`wiki_read`、`wiki_index`、`wiki_recent`（streamable HTTP，`/mcp`）
- scope 目录级权限（default-deny）：硬排除 → frontmatter `share: false` → scope 前缀
- 链接脱敏：指向不可见笔记的链接/嵌入替换为 `〔未公开链接〕`/`〔未公开内容〕`，检索在脱敏文本上进行；"无权"与"不存在"返回同一文案；代码块内的 `[[...]]` 不视为链接
- 结果附修改时间、frontmatter 状态，冻结目录标注"遗留参考"
- `tokens.yaml` 热加载（只存 token 的 sha256，改坏保留上一版有效配置）
- JSONL 访问日志、按用户限频、Host/Origin 校验，默认只监听本机，支持 TLS
- 后端插件：`obsidian`、`markdown`
- 命令行：`serve`、`token-new`、`check`、`preview`、`admin-url`
- 本机管理页面（`127.0.0.1:8766`）：新建同事、按目录授权、停用 / 重置 token / 删除、以某人身份预览、访问记录，一键复制接入说明
- Windows 启动脚本 `start.bat`、`admin.bat`

[Unreleased]: https://github.com/kevinliu623/wiki-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kevinliu623/wiki-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/kevinliu623/wiki-mcp/releases/tag/v0.1.0
