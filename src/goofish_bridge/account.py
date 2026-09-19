"""单账号进程初始化和人工登录；不自动探测其他浏览器身份。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from goofish_bridge.config import AccountPaths, Config
from goofish_bridge.network import NetworkProfile
from goofish_cli.core.client_profile import ClientProfile

_active_directory: Path | None = None


def initialize_account(config: Config, key: str) -> AccountPaths:
    """仅允许在独立账号命令进程启动时设置一次上游兼容路径。"""
    global _active_directory
    paths = AccountPaths(config.root, key)
    if not (config.raw.get("bitbrowser") or {}).get("enabled"):
        NetworkProfile(**config.account(key)["network"])
        ClientProfile(**config.account(key).get("client", {}))
    if _active_directory is not None and _active_directory != paths.directory:
        raise RuntimeError("禁止在同一进程切换账号，请启动新的命令进程")
    _active_directory = paths.directory
    paths.directory.mkdir(parents=True, exist_ok=True)
    os.environ["GOOFISH_COOKIES_PATH"] = str(paths.file("cookies.json"))
    os.environ["GOOFISH_NO_CHROME_BOOTSTRAP"] = "1"
    os.environ["GOOFISH_AUTO_REFRESH_TOKEN"] = "0"
    os.environ["GOOFISH_WRITE_RPM"] = "1"
    # 继承的 IM Token 可能属于另一个账号，禁止使用。
    os.environ.pop("GOOFISH_IM_TOKEN", None)
    from goofish_cli.core import guard, limiter, session, token

    session.DEVICE_CACHE_PATH = paths.file("device.json")
    token.TOKEN_CACHE = paths.file("im_token.json")
    guard.STATE_PATH = paths.file("circuit.json")
    limiter.STATE_PATH = paths.file("limiter.json")
    return paths


def _refresh_bitbrowser_cookie(config: Config, paths: AccountPaths):
    """装载会话时一次读取同窗口 Cookie、网络和 UA；初始化不重复刷新。"""
    settings = config.raw.get("bitbrowser") or {}
    if not settings.get("enabled"):
        return
    from goofish_bridge.bitbrowser_cookie import BitBrowserClient
    from goofish_cli.core.session import write_cookies_json

    base_url = settings.get("api_url", "http://127.0.0.1:54345")
    group_name = settings.get("group_name", "闲鱼")
    expected_uid = bound_uid(paths)
    records, network, client = BitBrowserClient(base_url).account_context(
        group_name, config.account(paths.key)["name"], expected_uid,
        **({"profile_id": config.account(paths.key)["bitbrowser_profile_id"]}
           if config.account(paths.key).get("bitbrowser_profile_id") else {}))
    pending = paths.file("cookies-bitbrowser-pending.json")
    write_cookies_json(pending, records)
    pending.replace(paths.file("cookies.json"))
    return network, client


def resolve_account_settings(config: Config, key: str):
    """扫码入口只取窗口配置，不读取或替换账号凭据。"""
    settings = config.raw.get("bitbrowser") or {}
    if settings.get("enabled"):
        from goofish_bridge.bitbrowser_cookie import BitBrowserClient

        api = BitBrowserClient(settings.get("api_url", "http://127.0.0.1:54345"))
        profile = api.profile_for_account(
            settings.get("group_name", "闲鱼"), config.account(key)["name"],
            **({"profile_id": config.account(key)["bitbrowser_profile_id"]}
               if config.account(key).get("bitbrowser_profile_id") else {}))
        return api.settings_for_profile(profile.profile_id)
    return (NetworkProfile(**config.account(key)["network"]),
            ClientProfile(**config.account(key).get("client", {})))


@contextmanager
def account_lock(paths: AccountPaths):
    """文件锁退出自动释放，保留锁文件，不删除运行数据。"""
    import msvcrt

    with paths.file("account.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("该账号已有登录或验证进程运行") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def bound_uid(paths: AccountPaths) -> str:
    binding = paths.file("binding.json")
    if not binding.exists():
        raise ValueError("账号尚未绑定，请先执行 login")
    uid = json.loads(binding.read_text(encoding="utf-8")).get("uid")
    if not isinstance(uid, str) or not uid:
        raise ValueError("本地账号绑定无效")
    return uid


def validate_uid(config: Config, paths: AccountPaths, uid: str) -> None:
    expected = config.account(paths.key).get("expected_uid", "")
    if expected.startswith("<"):
        raise ValueError("请先清除或填写 expected_uid 占位值")
    if expected and expected != uid:
        raise ValueError("凭据 UID 与配置不符，账号已停止")
    if paths.file("binding.json").exists() and bound_uid(paths) != uid:
        raise ValueError("凭据 UID 与原绑定不符，禁止改绑或消费旧任务")
    keys = {a["key"] for a in config.raw["accounts"]}
    keys.update(p.parent.name for p in (config.root / "accounts").glob("A*/binding.json"))
    for key in keys:
        other = AccountPaths(config.root, key)
        if key != paths.key and other.file("binding.json").exists() and bound_uid(other) == uid:
            raise ValueError("该 UID 已绑定到另一账号槽位")


def save_login(config: Config, paths: AccountPaths, records: list, confirmed_uid: str) -> None:
    from goofish_cli.core.session import _records_to_flat, write_cookies_json

    flat = _records_to_flat(records)
    uid = flat.get("unb", "")
    if not uid or not flat.get("_m_h5_tk") or confirmed_uid != uid:
        raise ValueError("凭据缺少关键字段或人工确认 UID 不一致，未保存")
    validate_uid(config, paths, uid)
    cookie_path = paths.file("cookies.json")
    if cookie_path.exists():
        shutil.copy2(cookie_path, paths.file(f"cookies-backup-{uuid4().hex}.bin"))
    pending = paths.file(f"cookies-pending-{uuid4().hex}.bin")
    write_cookies_json(pending, records)
    # 旧凭据已有完整备份，原子替换避免写入一半。
    pending.replace(cookie_path)
    if not paths.file("binding.json").exists():
        with paths.file("binding.json").open("x", encoding="utf-8") as handle:
            json.dump({"uid": uid, "confirmed_at": datetime.now(UTC).isoformat()},
                      handle, ensure_ascii=False)


def load_session(config: Config, paths: AccountPaths):
    from goofish_cli.core.session import Session, _load_cookies, _load_or_mint_device_id

    uid = bound_uid(paths)
    validate_uid(config, paths, uid)
    runtime_settings = _refresh_bitbrowser_cookie(config, paths)
    records = _load_cookies(paths.file("cookies.json"))
    flat = {r["name"]: r["value"] for r in records}
    if flat.get("unb") != uid or not flat.get("_m_h5_tk"):
        raise ValueError("当前凭据身份与绑定不符或缺少签名 Token")
    network, client = runtime_settings or resolve_account_settings(config, paths.key)
    http = network.http()
    http.cookies.update(flat)
    return Session(http, uid, flat.get("tracknick", ""),
                   _load_or_mint_device_id(uid, paths.file("device.json")), records,
                   client=client, websocket_connect=network.websocket)


async def scan_login(paths: AccountPaths, timeout: int = 180, *,
                     network: NetworkProfile | None = None) -> list:
    from playwright.async_api import async_playwright

    # 每次创建专属空目录，不导入本机浏览器状态；保留目录便于用户自行清理。
    profile = paths.directory / "profiles" / uuid4().hex
    profile.mkdir(parents=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), **(network or NetworkProfile()).browser_options(),
            locale="zh-CN", timezone_id="Asia/Shanghai",
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.goofish.com/login", wait_until="domcontentloaded")
            print("请在独立 Chrome 窗口中使用手机闲鱼扫码，并在手机确认。", flush=True)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                cookies = await context.cookies(["https://www.goofish.com"])
                flat = {c["name"]: c["value"] for c in cookies}
                if all(flat.get(k) for k in ("unb", "_m_h5_tk", "cookie2")):
                    return [{k: c[k] for k in ("name", "value", "domain", "path")} for c in cookies]
                await asyncio.sleep(1)
            raise TimeoutError("扫码登录超时，未保存凭据")
        finally:
            await context.close()
