"""Command line: ``python -m wiki_mcp <command>``.

serve      启动 MCP 服务（同时在本机启动管理页面）
admin-url  打印 / 打开管理页面链接
token-new  生成一个新 token（明文只显示这一次），输出可粘贴进 tokens.yaml 的片段
check      校验 config.yaml + tokens.yaml，报告各 scope / 用户可见的笔记数与可疑配置
preview    以某个用户的身份查看目录树 / 检索，用来核对权限是否符合预期
"""

from __future__ import annotations

import argparse
import sys

from .access import AccessError, AccessStore, generate_token
from .config import ConfigError, load_config
from .paths import segments


def _service(cfg):
    from .backends import create_backend
    from .service import WikiService

    return WikiService(cfg, create_backend(cfg.backend, cfg.root))


def cmd_serve(args) -> int:
    from .server import serve

    serve(load_config(args.config), open_admin=args.open)
    return 0


def cmd_admin_url(args) -> int:
    from .admin import admin_key, admin_key_path, admin_url

    cfg = load_config(args.config)
    if not cfg.admin_port:
        print("config.yaml 中 admin_port 为 0，管理页面未启用", file=sys.stderr)
        return 2
    url = admin_url(cfg.admin_port, admin_key(admin_key_path(cfg.access_file)))
    print(url)
    if args.open:
        import webbrowser
        webbrowser.open(url)
    return 0


def cmd_token_new(args) -> int:
    token, digest = generate_token()
    scopes = ", ".join(s.strip() for s in (args.scopes or "").split(",") if s.strip())
    print("新 token（只显示这一次，请直接发给对方，不要存进仓库）：")
    print(f"  {token}\n")
    print("把下面这段加到 tokens.yaml 的 users 下：")
    print(f"  - name: {args.name}")
    print(f"    token_sha256: {digest}")
    print(f"    scopes: [{scopes}]")
    return 0


def cmd_check(args) -> int:
    from .policy import Policy

    cfg = load_config(args.config)
    store = AccessStore(cfg.access_file)
    table = store.table()
    svc = _service(cfg)
    all_paths = svc.all_paths()
    warnings = 0
    print(f"知识库：{cfg.root}（backend={cfg.backend}），排除后可索引笔记 {len(all_paths)} 篇")
    print(f"监听：{cfg.scheme}://{cfg.host}:{cfg.port}/mcp")
    if cfg.scheme == "http" and cfg.host not in ("127.0.0.1", "localhost", "::1"):
        print("  ⚠ 明文 HTTP 对外监听：token 可被同网段嗅探，建议启用 TLS")
        warnings += 1
    print("\nScopes：")
    for name, prefixes in table.scopes.items():
        for p in prefixes:
            n = len(svc.visible_paths(Policy.from_prefixes([p])))
            flag = ""
            if svc.exclude.excluded(p) and segments(p):
                flag = "  ⚠ 该目录在排除列表内，授权无效"
                warnings += 1
            elif n == 0:
                flag = "  ⚠ 没有匹配到任何笔记（路径写错了？）"
                warnings += 1
            print(f"  {name}: {p or '.'} → {n} 篇{flag}")
    print("\nUsers：")
    for u in sorted(table.users.values(), key=lambda u: u.name):
        n = len(svc.visible_paths(u.policy))
        print(f"  {u.name}: scopes={list(u.scopes)} → 可见 {n} 篇")
    for name in sorted(table.disabled):
        print(f"  {name}: 已停用")
    print(f"\n检查完成，{warnings} 个警告。")
    return 1 if warnings else 0


def cmd_preview(args) -> int:
    cfg = load_config(args.config)
    table = AccessStore(cfg.access_file).table()
    user = table.user_by_name(args.user)
    if user is None:
        print(f"找不到启用中的用户：{args.user}", file=sys.stderr)
        return 2
    svc = _service(cfg)
    if args.search:
        print(svc.search(user.policy, args.search).text)
    elif args.read:
        print(svc.read(user.policy, args.read).text)
    else:
        print(svc.index(user.policy, args.path or "").text)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="wiki-mcp", description="只读、按 token 授权的知识库 MCP 服务")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="启动服务")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("--open", action="store_true", help="启动后在浏览器打开管理页面")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("admin-url", help="打印管理页面链接")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("--open", action="store_true", help="在浏览器打开")
    p.set_defaults(fn=cmd_admin_url)

    p = sub.add_parser("token-new", help="生成新 token")
    p.add_argument("name")
    p.add_argument("--scopes", help="逗号分隔的 scope 名")
    p.set_defaults(fn=cmd_token_new)

    p = sub.add_parser("check", help="校验配置并报告权限覆盖情况")
    p.add_argument("-c", "--config", default="config.yaml")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("preview", help="以某用户身份预览可见内容")
    p.add_argument("user")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("--path", help="wiki_index 的子目录")
    p.add_argument("--search", help="改为执行 wiki_search")
    p.add_argument("--read", help="改为执行 wiki_read")
    p.set_defaults(fn=cmd_preview)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (ConfigError, AccessError) as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
