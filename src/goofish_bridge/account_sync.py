"""启动前发现窗口账号；调用方持有消息桥单实例锁。"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from uuid import uuid4

from goofish_bridge.account import account_lock, bound_uid, save_login
from goofish_bridge.bitbrowser_cookie import BitBrowserClient, BitBrowserError
from goofish_bridge.config import AccountPaths, Config


def sync_accounts(config: Config, api: BitBrowserClient | None = None) -> Config:
    """按 UID 补建本地账号，不删除账号或改绑已有身份。"""
    settings = config.raw.get("bitbrowser") or {}
    if not settings.get("enabled"):
        return config
    api = api or BitBrowserClient(settings.get("api_url", "http://127.0.0.1:54345"))
    result = Config(config.root, deepcopy(config.raw))
    accounts = result.raw["accounts"]
    by_uid, by_profile = {}, {}
    # 同时对比磁盘绑定，避免遗漏配置外的已有账号。
    for binding in (config.root / "accounts").glob("A*/binding.json"):
        key = binding.parent.name
        if not any(a["key"] == key for a in accounts):
            accounts.append({"key": key, "name": key, "data_dir": f"accounts/{key}",
                             "expected_uid": bound_uid(AccountPaths(config.root, key))})
    for account in accounts:
        paths = AccountPaths(config.root, account["key"])
        uid = account.get("expected_uid", "")
        if paths.file("binding.json").exists():
            binding_uid = bound_uid(paths)
            if uid and uid != binding_uid:
                raise ValueError("本地账号配置与绑定身份冲突")
            uid = binding_uid
        if uid:
            if uid in by_uid:
                raise ValueError("本地多个账号绑定同一身份")
            by_uid[uid] = account
        profile_id = account.get("bitbrowser_profile_id")
        if profile_id:
            if profile_id in by_profile:
                raise ValueError("本地多个账号绑定同一窗口")
            by_profile[profile_id] = account
    profiles = api.profiles(settings.get("group_name", "闲鱼"))
    profiles.sort(key=lambda p: (p.profile_id not in by_profile, p.profile_id))
    seen, plans = set(), []
    for profile in profiles:
        try:
            records = api.cookies(profile.profile_id)
            BitBrowserClient.validate_records(records, profile.name, "")
        except BitBrowserError:
            logging.getLogger("goofish_bridge").warning(
                "跳过比特窗口 %s：凭据读取失败或未登录，请检查原窗口", profile.name)
            continue
        uid = next(r["value"] for r in records if r["name"] == "unb")
        pinned = by_profile.get(profile.profile_id)
        if pinned is not None and by_uid.get(uid) is not pinned:
            raise ValueError("比特窗口登录身份已改变，禁止自动改绑")
        if uid in seen:
            continue
        seen.add(uid)
        account = by_uid.get(uid)
        if account is None:
            # 仅复用没有身份的同名预留槽位；昵称不能覆盖既有 UID。
            candidates = [a for a in accounts if a["name"] == profile.name
                          and not a.get("bitbrowser_profile_id")
                          and a not in by_uid.values()]
            if len(candidates) == 1:
                account = candidates[0]
            else:
                number = max((int(a["key"][1:]) for a in accounts), default=0) + 1
                while (config.root / "accounts" / f"A{number}").exists():
                    number += 1
                account = {"key": f"A{number}", "data_dir": f"accounts/A{number}"}
                accounts.append(account)
            by_uid[uid] = account
        if account.get("bitbrowser_profile_id", profile.profile_id) != profile.profile_id:
            # 已有稳定来源不因重复窗口而变更。
            continue
        account.update(name=profile.name or account["key"], expected_uid=uid,
                       bitbrowser_profile_id=profile.profile_id)
        plans.append((account, records, uid))
    for account, records, uid in plans:
        paths = AccountPaths(config.root, account["key"])
        paths.directory.mkdir(parents=True, exist_ok=True)
        if not paths.directory.resolve().is_relative_to(config.root.resolve()):
            raise ValueError("账号目录不允许指向项目之外")
        with account_lock(paths):
            metadata = paths.file("account.json")
            content = json.dumps(account, ensure_ascii=False, indent=2)
            if not metadata.exists() or metadata.read_text(encoding="utf-8") != content:
                pending = paths.file(f"account-{uuid4().hex}.tmp")
                pending.write_text(content, encoding="utf-8")
                pending.replace(metadata)
            if not paths.file("binding.json").exists() or not paths.file("cookies.json").exists():
                save_login(result, paths, records, uid)
                logging.getLogger("goofish_bridge").info("自动建立本地账号 %s", account["key"])
    return result
