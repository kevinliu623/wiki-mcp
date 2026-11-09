# wiki-mcp

> 把你的个人 Obsidian / Markdown 知识库，变成同事 AI agent 可以按权限查询的只读 MCP 服务。
>
> Turn your personal Obsidian / Markdown knowledge base into a read-only, permission-scoped MCP service that teammates' AI agents can query.

**为什么做这个**：个人知识库里的设计决策、技术文档、项目进度，同事的 agent 用不上——要么给你发消息问，要么干脆不知道有。wiki-mcp 把知识库原样封一层 MCP：**知识库保持你自己的习惯维护，同事的 agent 按权限查询**。文件就是活文件，你刚写完的内容立刻可查，没有任何同步环节。

## 特性

- **4 个只读工具**，单次返回服务端封顶，防止把 agent 上下文撑爆：

| 工具 | 说明 |
|---|---|
| `wiki_search(query, limit)` | 词法检索（多词需全部命中），返回路径 + 修改时间/状态 + 命中片段，永不整页 |
| `wiki_read(path, offset)` | 读全文；`path` 可只写标题；超长分段续读；首段附过滤后的出链/反链 |
| `wiki_index(path)` | 按权限动态生成的两级目录树（含各目录篇数） |
| `wiki_recent(days, limit)` | 最近 N 天修改的笔记（"最近进展"类问题的直接答案） |

- **按 token 的目录级权限（default-deny）**：`tokens.yaml` 定义 scope（一组目录），每个人分配若干 scope；列表、搜索、目录树、最近修改、关联笔记**全部按 token 过滤**
- **不泄漏受限内容的名字**：可见笔记里指向受限/不存在笔记的链接与嵌入替换为 `〔未公开链接〕`；检索在替换后的文本上进行，无法通过搜索探测受限笔记名；"无权访问"与"不存在"返回同一句话
- **硬排除区**：配置的 `exclude` 目录与所有 `.xxx` 隐藏目录对任何人不可见（scope 写错也进不去）；单篇笔记 frontmatter 写 `share: false` 即对所有人隐藏
- **结果带时效**：每条结果附最后修改时间与 frontmatter 状态；冻结目录标注"遗留参考，可能过时"
- **tokens.yaml 热加载**：加人、改权限、吊销，保存即生效；改坏了继续使用上一版有效配置；只存 token 的 sha256
- **访问日志**：每次调用一行 JSONL（时间、用户、IP、工具、参数摘要、结果、耗时），含鉴权失败与越权
- **零后台、零数据库**：直接读 Markdown 文件，不需要 Obsidian 运行，不需要向量库
- **多种知识库类型**：后端插件化，当前支持 `obsidian`、`markdown`（普通 Markdown 文件夹）
- **本机管理页面**：新建同事、勾选目录授权、停用 / 重置 token、以某人身份预览、查看访问记录；生成 token 时一键复制"发给对方的接入说明"（含 MCP 配置与建议提示词）

## 架构

```
同事的 agent（带各自 Bearer token）
        │  http(s)://<内网IP>:8765/mcp
        ▼
   BearerAuthMiddleware ── 无效 token → 401（记日志）
        ▼
   MCPServer（streamable HTTP，Host/Origin 校验，按用户限频）
        │  4 个只读工具 → WikiService
        ▼
   WikiService：可见性（硬排除 → share:false → scope）+ 链接脱敏 + 返回预算
        ▼
   Backend（obsidian / markdown）：列举、读取、解析 frontmatter 与链接
        ▼
   你的 Markdown 知识库（活文件，无同步）
```

## 快速开始

```bash
# 1. 安装（Python ≥ 3.10）
python -m venv .venv
.venv/Scripts/pip install -e .          # Linux/macOS: .venv/bin/pip

# 2. 配置
cp config.example.yaml config.yaml      # 改 root、exclude、host
mkdir data && cp tokens.example.yaml data/tokens.yaml   # 定义 scopes（运行时状态都在 data/，不入库）

# 3. 启动（Windows 可直接运行 start.bat）
python -m wiki_mcp serve --open          # --open：同时在浏览器打开管理页面
```

启动日志会打印**管理页面**链接（`http://127.0.0.1:8766/#key=…`，只有本机能访问）。在页面上：

- **新建同事**：填名字 → 勾选能看的目录或已有范围 → 生成 token，复制"发给对方的完整说明"私下发给他
- **编辑权限 / 停用 / 重置 token / 删除**：保存即生效，无需重启
- **预览**：以某人的身份看目录、试检索，核对权限是否符合预期
- **访问记录**：谁、何时、查了什么，可只看越权 / 认证失败等异常

忘了链接：双击 `admin.bat`，或运行 `python -m wiki_mcp admin-url --open`。

也可以不用页面，直接编辑 `data/tokens.yaml` 或用命令行：

```bash
python -m wiki_mcp token-new 张三 --scopes 技术公共,项目A   # 输出 token 和要粘进 tokens.yaml 的片段
python -m wiki_mcp check                                   # 各 scope / 用户可见篇数、可疑配置
python -m wiki_mcp preview 张三 --search "部署"             # 以张三身份检索
```

同事接入：在他们的 MCP 客户端（Claude Code / Codex / Cursor 等）配置里加：

```json
{
  "mcpServers": {
    "wiki": {
      "type": "http",
      "url": "http://<内网IP>:8765/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

### Docker 部署（推荐长期运行）

容器开机自启、崩溃自动重启，在 Docker Desktop 里能直接看到运行状态和日志。与本机运行共用同一份 `config.yaml` 和 `data/`：

```bash
cp .env.example .env              # 填 WIKI_VAULT=知识库根目录
docker compose up -d --build      # 启动 / 改代码后重建
docker compose logs -f            # 每次查询、鉴权失败、管理操作各打印一行
docker compose down               # 停止
```

不想本地构建：在 `.env` 里加 `WIKI_MCP_IMAGE=ghcr.io/kevinliu623/wiki-mcp:0.2.0`，然后 `docker compose pull && docker compose up -d`。

- 知识库只读挂载到容器的 `/vault`，`config.yaml` 只读挂载，`data/`（tokens、管理口令、访问日志）读写挂载
- 容器内自动把 `root` 换成 `/vault`、监听 `0.0.0.0`；`config.yaml` 里写的 `host` 仍是同事访问的地址（自动加入 `allowed_hosts`）
- 管理页面端口只发布到宿主机 `127.0.0.1:8766`，链接照旧（`admin.bat` 也照常可用）
- 容器与 `start.bat` 不要同时运行（端口冲突）
- Windows/macOS 上目录挂载较慢（扫描一次约 1 秒），compose 把重扫间隔设为 30 秒：笔记改动最多 30 秒后可见
- 已知限制：Docker Desktop 的端口转发不保留来源 IP，访问日志里的 `ip` 都是容器网关地址；按 token 记录的**用户名不受影响**

## 安全模型

- **default-deny**：token 没授权的目录，读、搜、列、最近修改、关联笔记全部不可见
- **判定顺序**：硬排除 → `share: false` → scope 前缀（按目录逐级匹配，`项目/2026` 不会匹配 `项目/20260101-x`）
- **路径安全**：只提供扫描得到的 `.md` 文件，不按用户输入拼文件路径；不跟随符号链接与 junction
- **传输**：默认只监听 `127.0.0.1`；监听 `0.0.0.0` 必须配置 `allowed_hosts`；开启 Host/Origin 校验防 DNS rebinding；明文 HTTP 对外时启动告警，建议配置 `tls_certfile/tls_keyfile` 或置于 HTTPS 反向代理之后
- **凭据**：`tokens.yaml` 只存 sha256；`config.yaml`、`tokens.yaml`、日志均不入库
- **限频与审计**：每用户每分钟调用上限；所有调用、鉴权失败与管理操作写访问日志（不记录明文 token）
- **管理页面**：只监听 `127.0.0.1`（容器内监听容器网卡，但只发布到宿主机 `127.0.0.1`）、独立端口；所有接口需要 `X-Admin-Key` 请求头（跨站网页无法伪造，防 CSRF），校验 Host 头；严格 CSP；口令保存在 `tokens.yaml` 旁的 `.admin_key`（不入库）
- **已知边界**：只隐藏*链接*，不隐藏正文里的普通文字提及（包括代码块里的文字）；附件（图片/PDF）不提供，但附件链接文字保持原样

## 开发

```bash
.venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest              # 全部测试（含真实 HTTP 端到端）
.venv/Scripts/python -m pytest tests/test_service.py -k search
```

设计决策与讨论记录见 [docs/design.md](docs/design.md)。

## Roadmap

- [x] v0.1 只读查询：4 个工具、scope 权限、链接脱敏、热加载、审计、限频、本机管理页面
- [x] v0.2 Docker 部署（镜像 `ghcr.io/kevinliu623/wiki-mcp`）
- [ ] 需求投递（按项目收件箱 + 定时扫描确认，见 design.md D1）
- [ ] wiki 汇总层按页面 `projects:` 字段授权
- [ ] 更多后端：Logseq、思源笔记
- [ ] 语义检索（分词/embedding，词法不够用时）

## Contributing

欢迎 issue 和 PR——接入你手头的 MCP 客户端、报告检索盲区、贡献新的知识库后端都非常有价值。新后端只需实现 `wiki_mcp/backends/base.py` 的 `Backend` 接口。

## License

[MIT](LICENSE)
