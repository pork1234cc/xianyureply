"""Windows 命令入口；阶段 0 验证完成前不开放常驻转发。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from importlib.metadata import version
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import unquote

from goofish_bridge.config import AccountPaths, Config
from goofish_bridge.network import NetworkProfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def doctor(config: Config) -> int:
    checks = []
    for name in ("requests", "websockets", "lark-oapi", "playwright"):
        checks.append({"检查": name, "版本": version(name), "通过": True})
    checks.append({"检查": "Node.js", "通过": bool(shutil.which("node"))})
    checks.append({"检查": "项目虚拟环境", "通过": Path(sys.prefix) == config.root / ".venv"})
    for account in config.raw["accounts"]:
        NetworkProfile(**account["network"])
        paths = AccountPaths(config.root, account["key"])
        checks.append({"检查": f"{paths.key} 凭据与人工绑定",
                       "通过": paths.file("cookies.json").exists()
                       and paths.file("binding.json").exists()})
    feishu = config.raw["feishu"]
    NetworkProfile(**feishu["network"])
    for field in ("app_id_env", "app_secret_env", "allowed_open_id_env"):
        checks.append({"检查": f"环境变量 {feishu[field]}", "通过": bool(os.getenv(feishu[field]))})
    print(json.dumps({"阶段": "MVP 开发版；本地准备检查不代表完整运行验收", "检查结果": checks},
                     ensure_ascii=False, indent=2))
    return 0 if all(item["通过"] for item in checks) else 2


def login(config: Config, args) -> int:
    from goofish_bridge.account import (
        account_lock,
        initialize_account,
        save_login,
        scan_login,
        validate_uid,
    )
    from goofish_cli.core.session import _load_cookies, _records_to_flat

    paths = initialize_account(config, args.account)
    with account_lock(paths):
        records = asyncio.run(scan_login(paths, args.timeout)) if args.qr else _load_cookies(args.cookies)
        flat = _records_to_flat(records)
        uid = flat.get("unb", "")
        if not uid:
            raise ValueError("登录凭据未包含 UID")
        validate_uid(config, paths, uid)
        nickname = unquote(flat.get("tracknick") or "昵称未知")
        print(f"账号 {args.account} 的 Cookie 声明 UID：{uid}；昵称：{nickname}")
        print("请核对手机当前账号。服务端身份及收发能力仍需后续验证。", flush=True)
        confirmed_uid = input("请输入上面完整 UID 确认绑定（其他输入取消）：").strip()
        save_login(config, paths, records, confirmed_uid)
        print(f"已保存 {args.account} 独立加密凭据和人工绑定。")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="闲鱼飞书消息桥：阶段 0 验证工具")
    root.add_argument("--config", type=Path, default=Path("config.yaml"))
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="本地检查，不向客户发送消息")
    sub.add_parser("status", help="显示本地准备状态")
    auth = sub.add_parser("login", help="指定账号扫码或导入 Cookie")
    auth.add_argument("--account", required=True, choices=["A1", "A2", "A3"])
    source = auth.add_mutually_exclusive_group(required=True)
    source.add_argument("--qr", action="store_true")
    source.add_argument("--cookies", type=Path)
    auth.add_argument("--timeout", type=int, default=180)
    for name in ("probe-watch", "probe-history", "probe-roundtrip", "probe-route"):
        probe = sub.add_parser(name, help="单账号真实协议验证")
        probe.add_argument("--account", required=True, choices=["A1", "A2", "A3"])
        probe.add_argument("--seconds", type=int, default=60)
        if name in {"probe-history", "probe-roundtrip"}:
            probe.add_argument("--cid", required=True)
        if name == "probe-route":
            probe.add_argument("--marker", required=True)
        if name == "probe-history":
            probe.add_argument("--compare-message-id", default="")
            probe.add_argument("--compare-source-time", default="")
        if name == "probe-roundtrip":
            probe.add_argument("--customer-uid", required=True)
            probe.add_argument("--text", required=True)
            probe.add_argument("--confirm-test-target", action="store_true", required=True,
                               help="确认目标为本人控制的测试客户和已有会话")
    export = sub.add_parser("export-samples", help="导出脱敏协议结构，不导出发送目标")
    export.add_argument("--output", type=Path, required=True)
    for name in ("bind-feishu", "probe-feishu"):
        feishu = sub.add_parser(name, help="验证飞书本人私聊与直接引用事件")
        feishu.add_argument("--seconds", type=int, default=180)
    sub.add_parser("feishu-test-message", help="向已确认的本人私聊发送一条固定引用验证消息")
    runtime = sub.add_parser("run", help="启动已绑定账号的双向消息桥")
    runtime.add_argument("--accounts", nargs="+", choices=["A1", "A2", "A3"], default=["A1", "A2", "A3"])
    runtime.add_argument("--test-customer-uid", help="只接收/发送指定测试客户，使用独立测试数据库")
    runtime.add_argument("--duration", type=int, help="运行指定秒数后安全退出")
    sub.add_parser("stop", help="请求正在运行的消息桥安全退出")
    return root


def probe(config: Config, args) -> int:
    from goofish_bridge.account import account_lock, initialize_account, load_session
    from goofish_bridge.goofish_adapter import (
        capture_test_route,
        probe_history,
        probe_roundtrip,
        probe_watch,
    )
    from goofish_bridge.probe_store import ProbeStore

    if not 1 <= args.seconds <= 600:
        raise ValueError("观察时间必须在 1～600 秒之间")
    paths = initialize_account(config, args.account)
    with account_lock(paths):
        store = ProbeStore(config.root)
        session = None
        try:
            store.recover_account(args.account)
            session = load_session(config, paths)
            if args.command == "probe-route":
                result = asyncio.run(capture_test_route(session, store, args.marker, args.seconds))
            elif args.command == "probe-watch":
                result = asyncio.run(probe_watch(session, store, args.seconds))
            elif args.command == "probe-history":
                result = asyncio.run(probe_history(
                    session, store, args.cid, timeout=min(args.seconds, 60),
                    compare_message_id=args.compare_message_id,
                    compare_source_time=args.compare_source_time,
                ))
            else:
                result = asyncio.run(probe_roundtrip(session, store, args.account, args.cid,
                                                    args.customer_uid, args.text, args.seconds))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        finally:
            if session:
                session.http.close()
            store.close()


def main() -> int:
    args = parser().parse_args()
    from loguru import logger

    # 上游异常可能包含认证 URL 或原始包；诊断工具不启用这些日志。
    logger.disable("goofish_cli")
    try:
        config = Config.load(args.config)
        if args.command == "doctor":
            return doctor(config)
        if args.command == "status":
            database = (config.root / config.raw["app"]["database"]).resolve()
            if not database.exists():
                print("正式业务库尚未初始化。")
                return doctor(config)
            with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as db:
                for row in db.execute("SELECT account_key,state,error FROM accounts ORDER BY account_key"):
                    print(f"{row[0]}：{row[1]} {row[2]}")
                counts = dict(db.execute("SELECT state,count(*) FROM reply_tasks GROUP BY state"))
                print(json.dumps(counts, ensure_ascii=False))
            return 0
        if args.command == "stop":
            stop_path = config.root / "data" / "stop.request"
            stop_path.parent.mkdir(exist_ok=True)
            stop_path.write_text(str(time.time()), encoding="utf-8")
            print("已请求消息桥安全退出。")
            return 0
        if args.command == "run":
            from goofish_bridge.supervisor import run

            log_dir = config.root / "logs"
            log_dir.mkdir(exist_ok=True)
            handler = RotatingFileHandler(log_dir / "bridge.log", maxBytes=2_000_000,
                                          backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            bridge_logger = logging.getLogger("goofish_bridge")
            bridge_logger.setLevel(logging.INFO)
            bridge_logger.addHandler(handler)
            if args.duration is not None and not 1 <= args.duration <= 86400:
                raise ValueError("运行时长应在 1～86400 秒之间")
            if len(set(args.accounts)) != len(args.accounts):
                raise ValueError("启动账号不能重复")
            bridge_logger.info("启动账号：%s", ",".join(args.accounts))
            try:
                return run(config, args.config, args.accounts,
                           test_customer=args.test_customer_uid, duration=args.duration) or 0
            finally:
                bridge_logger.info("主进程退出")
                handler.close()
                bridge_logger.removeHandler(handler)
        if args.command == "login":
            if not 1 <= args.timeout <= 600:
                raise ValueError("扫码超时必须在 1～600 秒之间")
            return login(config, args)
        if args.command in {"bind-feishu", "probe-feishu", "feishu-test-message"}:
            from goofish_bridge.feishu_adapter import (
                bind_feishu,
                load_binding,
                probe_events,
                send_test_message,
            )
            from goofish_bridge.probe_store import ProbeStore

            if args.command == "feishu-test-message":
                print(json.dumps(send_test_message(config), ensure_ascii=False, indent=2))
                return 0
            if not 1 <= args.seconds <= 600:
                raise ValueError("飞书验证时间必须在 1～600 秒之间")
            if args.command == "bind-feishu":
                bind_feishu(config, args.seconds)
                return 0
            store = ProbeStore(config.root)
            try:
                events = asyncio.run(probe_events(config, store, args.seconds, load_binding(config)))
                print(json.dumps(events, ensure_ascii=False, indent=2))
            finally:
                store.close()
            return 0
        if args.command.startswith("probe-"):
            return probe(config, args)
        if args.command == "export-samples":
            from goofish_bridge.probe_store import ProbeStore

            store = ProbeStore(config.root)
            try:
                store.export(args.output)
            finally:
                store.close()
            print("已导出脱敏结构；这些样本不代表真实业务验收已完成。")
            return 0
        print("未知命令。")
        return 2
    except (ValueError, RuntimeError, NotImplementedError, FileNotFoundError, TimeoutError) as exc:
        print(f"操作停止：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已安全退出。")
        return 130
    except Exception as exc:
        # 不输出潜在包含凭据的第三方异常正文。
        print(f"操作停止：{type(exc).__name__}；请检查本地环境或重新人工登录。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
