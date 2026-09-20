"""实例解释器内的只读配置检查；stdout 仅输出脱敏 JSON。"""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from dotenv import dotenv_values

from goofish_bridge.config import Config

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def inspect(root: Path) -> dict:
    # 已有 .env 明确优先，防止 Agent 的另一个实例环境变量串入。
    for key, value in dotenv_values(root / ".env", encoding="utf-8").items():
        os.environ[key] = value or ""
    config = Config.load(root / "config.yaml")
    checks = {"environment": Path(sys.prefix).resolve() == (root / ".venv").resolve(),
              "node": bool(shutil.which("node"))}
    for package in ("requests", "websockets", "lark-oapi", "playwright", "pillow"):
        try:
            importlib.metadata.version(package)
            checks[package] = True
        except importlib.metadata.PackageNotFoundError:
            checks[package] = False
    for field in ("app_id_env", "app_secret_env", "allowed_open_id_env"):
        checks[field] = bool(os.getenv(config.raw["feishu"][field]))
    accounts = config.raw["accounts"]
    checks["accounts"] = bool(accounts)
    checks["account_bindings"] = bool(accounts) and all(
        (root / "accounts" / a["key"] / "binding.json").is_file()
        and (root / "accounts" / a["key"] / "cookies.json").is_file() for a in accounts)
    checks["feishu_binding"] = (root / "data" / "feishu-binding.json").is_file()
    database = (root / config.raw["app"]["database"]).resolve()
    if not database.is_relative_to(root):
        raise ValueError("业务数据库必须在实例目录内")
    queue, database_accounts, schema = None, [], None
    if database.is_file():
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=2) as db:
            schema = db.execute("PRAGMA user_version").fetchone()[0]
            if schema == 4:
                queue = dict(db.execute("SELECT state,count(*) FROM reply_tasks GROUP BY state"))
                database_accounts = [{"key": k, "state": s} for k, s in db.execute(
                    "SELECT account_key,state FROM accounts ORDER BY account_key")]
            else:
                checks["database_schema"] = False
    return {"checks": checks, "queue": queue, "database_accounts": database_accounts,
            "database": str(database.relative_to(root)), "database_schema": schema,
            "configured_accounts": [a["key"] for a in accounts]}


def main():
    root = Path(sys.argv[1]).resolve()
    if "--run" in sys.argv[2:]:
        # 启动与探针使用相同的实例 .env 优先级。
        for key, value in dotenv_values(root / ".env", encoding="utf-8").items():
            os.environ[key] = value or ""
        from goofish_bridge.__main__ import main as bridge_main
        sys.argv = [sys.argv[0], "--config", str(root / "config.yaml"), "run"]
        raise SystemExit(bridge_main())
    try:
        result = inspect(root)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        result = {"checks": {"configuration": False}, "queue": None,
                  "database_accounts": [], "database_schema": None}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
