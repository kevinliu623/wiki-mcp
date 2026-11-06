# wiki-mcp

> 把你的个人 Obsidian / Markdown 知识库，变成同事 AI agent 可以查询、可以投递需求的只读 MCP 服务。
>
> Turn your personal Obsidian / Markdown knowledge base into a read-only MCP service that teammates' AI agents can query — and drop requests into.

**为什么做这个**：个人知识库里的设计决策、技术文档、项目进度，同事的 agent 用不上——要么给你发消息问，要么干脆不知道有。wiki-mcp 把知识库原样封一层 MCP：**知识库保持你自己的习惯维护，同事的 agent 按权限查询**。文件就是活文件，你刚写完的内容立刻可查，没有任何同步环节。

## 特性

- **8 个只读为主的安全工具**，单次返回服务端封顶，防止把 agent 上下文撑爆：

| 工具 | 说明 |
|---|---|
| `wiki_search(query)` | 词法检索，返回文件名+命中片段，永不整页 |
| `wiki_read(path)` | 读文件（超长自动截断为头部+区间说明） |
| `wiki_index()` | 知识库两级目录树 + 入口索引摘要 |
| `wiki_questions(query)` | 已沉淀问答检索 |
| `wiki_recent(days)` | 最近 N 天修改的笔记（进度类问题的直接答案） |
| `wiki_related(path)` | 沿 [[wikilink]] 列出关联笔记，agent 可顺藤摸瓜 |
| `wiki_ask(question)` | 查不到时把问题投进你的收件箱——你看到的是「大家想知道什么」 |
| `inbox_write(title, content)` | 需求投放：服务端控制落盘路径，带写者身份留痕 |

- **按 token 的目录级权限（default-deny）**：一个 YAML 文件管理所有同事，一行一个人，`allow` 列出他能看的目录前缀；搜索、目录树、最近修改**全部按 token 过滤**——受限的人连搜都搜不出范围外的内容
- **唯一写入口 `inbox_write`**：只认服务端生成的路径（`_inbox/<人名>/<时间戳>-<标题>.md`），不能指定路径、不能覆盖他人文件、带大小与频次上限
- **硬编码排除区**：`personal/` 类目录对所有人不可见（配置写错也进不去，防呆）
- **tokens.yaml 热加载**：加人、改权限、吊销，改完即生效不重启
- **访问日志**：谁在何时查了什么（JSONL 追加），审计兼兴趣信号——哪些文档被同事高频查看
- **零后台、零数据库**：直接读 Markdown 文件，不需要 Obsidian 运行，不需要向量库

## 架构

```
同事的 agent（带各自 Bearer token）
        │  http://<内网IP>:8765/mcp
        ▼
   server.py（FastMCP / streamable HTTP）
        │  ① 查 tokens.yaml：token → {name, allow[], inbox}
        │  ② 每个工具调用先过权限，搜索也按 token 过滤
        │  ③ personal/ 硬排除，写入只落 _inbox/
        ▼
   直接读你的 Markdown 知识库（活文件，无同步）
```

## 快速开始

```bash
# 1. 安装
pip install "mcp[cli]" pyyaml
# （Windows：配合 start.bat 设置 PYTHONUTF8=1 启动）

# 2. 配置 tokens.yaml（从 tokens.example.yaml 复制）
# 3. 启动
python server.py
```

同事接入：在他们的 MCP 客户端（Claude Code / Codex / Cursor 等）配置里加：

```json
{
  "mcpServers": {
    "wiki": {
      "url": "http://<内网IP>:8765/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

## 安全模型

- **default-deny**：token 没给的目录，读、搜、列、最近修改全部不可见
- **硬排除**：`deny` 目录优先于一切配置（写错配置也进不去）
- **写入面最小化**：仅一个 `inbox_write`，路径服务端生成、身份留痕、限大小限频次
- **全量审计**：每个工具调用记访问日志（时间、token 名、工具、参数摘要、是否越权被拒）
- **返回预算**：单次调用返回内容封顶，防止 agent 上下文被撑爆

## Roadmap

- [ ] v1.x 语义检索（分词/embedding，词法不够用时）
- [ ] v1.x 投递回执（写者可查自己投递的处理状态）
- [ ] v1.x 投递通知（本机弹窗 / 推送）
- [ ] v1.x 项目概览聚合（设计+进度+最近变化打包为一段上下文）
- [ ] 首个可用版本

## Contributing

欢迎 issue 和 PR——这是个大伙一起把它做好的项目：接入你手头的 MCP 客户端、报告检索盲区、贡献搜索改进都非常有价值。

## License

[MIT](LICENSE)
