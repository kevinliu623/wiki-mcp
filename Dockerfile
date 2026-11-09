# wiki-mcp 容器镜像。用法见 README「Docker 部署」与 compose.yaml。
#
# 容器约定（与 compose.yaml 一致）：
#   /vault                       知识库，只读挂载
#   /srv/wiki-mcp/config.yaml    与本机运行共用的 config.yaml，只读挂载
#   /srv/wiki-mcp/data/          tokens.yaml、.admin_key、logs/（管理页面要写，读写挂载）
FROM python:3.12-slim

ARG VERSION=dev
LABEL org.opencontainers.image.source="https://github.com/kevinliu623/wiki-mcp" \
      org.opencontainers.image.description="Read-only, token-scoped MCP server for an Obsidian / Markdown knowledge base" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY wiki_mcp ./wiki_mcp
RUN pip install . && rm -rf /build

# 不用 root 运行；data/ 由 compose 挂载
RUN useradd --system --uid 10001 --home-dir /srv/wiki-mcp --create-home wiki
USER wiki
WORKDIR /srv/wiki-mcp

# 同一份 config.yaml：容器里改用挂载的知识库路径、监听容器网卡。
# config.yaml 里写的 host 仍作为同事访问地址（自动加入 allowed_hosts）。
# 管理页面在容器内监听 0.0.0.0，由 compose 只发布到宿主机的 127.0.0.1。
ENV WIKI_MCP_ROOT=/vault \
    WIKI_MCP_HOST=0.0.0.0 \
    WIKI_MCP_ADMIN_BIND=0.0.0.0

EXPOSE 8765 8766

# 只做 TCP 探测：HTTP 探测会在访问日志里留下 auth_failed
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8765), 3)"]

CMD ["python", "-m", "wiki_mcp", "serve", "-c", "/srv/wiki-mcp/config.yaml"]
