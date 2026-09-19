"""配置和账号目录；所有相对路径均相对于配置文件。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class AccountPaths:
    root: Path
    key: str

    def __post_init__(self):
        if not isinstance(self.key, str) or not re.fullmatch(r"A[1-9][0-9]*", self.key):
            raise ValueError("账号编号必须为 A 加正整数，例如 A1、A5")

    @property
    def directory(self) -> Path:
        return self.root / "accounts" / self.key

    def file(self, name: str) -> Path:
        if Path(name).name != name:
            raise ValueError("账号文件名不允许包含目录")
        return self.directory / name


@dataclass(frozen=True)
class Config:
    root: Path
    raw: dict

    @classmethod
    def load(cls, path: Path) -> Config:
        path = path.resolve()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("配置必须为 YAML 对象")
        accounts = raw.get("accounts")
        if not isinstance(accounts, list) or not all(isinstance(a, dict) for a in accounts):
            raise ValueError("accounts 必须为账号列表，可为空")
        if len({a.get("key") for a in accounts}) != len(accounts):
            raise ValueError("账号编号缺失或重复")
        # 自动发现记录独立保存，保留用户 YAML 配置及其注释。
        for metadata in sorted((path.parent / "accounts").glob("A*/account.json")):
            discovered = json.loads(metadata.read_text(encoding="utf-8"))
            if discovered.get("key") != metadata.parent.name:
                raise ValueError("自动发现账号目录与编号不一致")
            existing = next((a for a in accounts if a.get("key") == discovered["key"]), None)
            if existing is None:
                accounts.append(discovered)
            else:
                expected = existing.get("expected_uid", "")
                if expected and expected != discovered.get("expected_uid"):
                    raise ValueError("自动发现账号与配置身份不一致")
                existing.update(discovered)
        uids = []
        for account in accounts:
            expected = account.get("expected_uid", "")
            if not isinstance(expected, str):
                raise ValueError("expected_uid 必须使用引号保存为字符串")
            if expected and not expected.startswith("<"):
                uids.append(expected)
            paths = AccountPaths(path.parent, account["key"])
            actual = (path.parent / account["data_dir"]).resolve()
            if actual != paths.directory.resolve():
                raise ValueError("每个账号必须使用项目内独立 accounts/编号 目录")
            if not actual.is_relative_to(path.parent):
                raise ValueError("账号目录不允许通过链接指向项目之外")
        if len(set(uids)) != len(uids):
            raise ValueError("不同账号不能绑定同一 UID")
        load_dotenv(path.parent / ".env", override=False, encoding="utf-8")
        return cls(path.parent, raw)

    def account(self, key: str) -> dict:
        AccountPaths(self.root, key)
        return next(a for a in self.raw["accounts"] if a["key"] == key)

    def initialize_directories(self) -> None:
        for account in self.raw["accounts"]:
            AccountPaths(self.root, account["key"]).directory.mkdir(parents=True, exist_ok=True)
        for name in ("data", "logs"):
            (self.root / name).mkdir(exist_ok=True)
