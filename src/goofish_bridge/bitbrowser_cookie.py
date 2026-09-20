"""从比特浏览器指定分组读取闲鱼账号 Cookie。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from goofish_bridge.network import NetworkProfile
from goofish_cli.core.client_profile import ClientProfile


class BitBrowserError(RuntimeError):
    """比特浏览器本地 API 返回失败或数据不符合预期。"""


@dataclass(frozen=True)
class BitBrowserProfile:
    profile_id: str
    name: str
    group_name: str


class BitBrowserClient:
    """比特浏览器 Local API 客户端，只实现本项目需要的只读接口。"""

    def __init__(self, base_url: str, timeout: float = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        try:
            # 多账号并发启动可能触发本地 API 的 429，只对读取接口做有界退避。
            readonly = path in {"/group/list", "/browser/list", "/browser/detail",
                                "/browser/cookies/get", "/browser/pids"}
            for attempt in range(5):
                response = requests.post(self.base_url + path, json=body, timeout=self.timeout)
                if response.status_code != 429 or not readonly or attempt == 4:
                    break
                delay = 2 ** attempt
                retry_after = response.headers.get("Retry-After", "")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        # 非秒数的等待提示不猜测，保留错误交由操作者处理。
                        break
                    if not 0 <= delay <= 30:
                        break
                response.close()
                time.sleep(delay)
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise BitBrowserError(f"比特浏览器 API 不可用：{path}") from exc
        if not isinstance(result, dict) or result.get("success") is not True:
            raise BitBrowserError(f"比特浏览器 API 返回失败：{path}：{result.get('msg', '')}")
        return result.get("data")

    def _list_all(self, path: str, **filters) -> list[dict]:
        result, seen = [], set()
        for page in range(1000):
            data = self._post(path, {"page": page, "pageSize": 100, **filters}) or {}
            items = data.get("list") if isinstance(data, dict) else None
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                raise BitBrowserError("比特列表格式无效")
            for item in items:
                identity = item.get("id")
                if not isinstance(identity, str) or not identity or identity in seen:
                    raise BitBrowserError("比特列表 ID 缺失或分页重复，请重试")
                seen.add(identity)
                result.append(item)
            if len(items) < 100:
                return result
        raise BitBrowserError("比特列表超过分页上限")

    def profiles(self, group_name: str) -> list[BitBrowserProfile]:
        groups = [item for item in self._list_all("/group/list")
                  if item.get("groupName") == group_name]
        if len(groups) != 1:
            raise BitBrowserError(f"比特浏览器分组不存在或重名：{group_name}")
        profiles = self._list_all("/browser/list", groupId=groups[0]["id"])
        result = []
        for item in profiles:
            profile_id = item.get("id")
            if profile_id and (not item.get("platform") or "goofish.com" in item["platform"]):
                result.append(BitBrowserProfile(profile_id, item.get("name", ""), group_name))
        return result

    def open_headless(self, profile_id: str) -> Any:
        """以无头模式打开窗口，使实时 Cookie 接口可用。"""
        return self._post("/browser/open", {"id": profile_id, "args": ["--headless"],
                                      "queue": True, "ignoreDefaultUrls": True})

    def close(self, profile_id: str) -> None:
        """关闭本模块临时打开的窗口。"""
        self._post("/browser/close", {"id": profile_id})
        for _ in range(10):
            pids = self._post("/browser/pids", {"ids": [profile_id]}) or {}
            if isinstance(pids, dict) and not pids.get(profile_id):
                return
            time.sleep(0.5)
        raise BitBrowserError("临时无头窗口未确认关闭，请检查比特浏览器")

    @staticmethod
    def _fresh(records: list[dict[str, Any]]) -> bool:
        """检查关键字段和 H5 令牌毫秒到期时间，预留一分钟启动余量。"""
        flat = {item.get("name"): item.get("value") for item in records}
        if not all(flat.get(name) for name in ("unb", "cookie2", "_m_h5_tk")):
            return False
        try:
            expires = int(flat["_m_h5_tk"].rsplit("_", 1)[1]) / 1000
        except (ValueError, IndexError, AttributeError, TypeError):
            return False
        return expires > time.time() + 60

    def _load_headless_page(self, opened: Any) -> list[dict[str, Any]]:
        """加载闲鱼页面，让浏览器刷新登录凭据，最多等待一分钟。"""
        from playwright.sync_api import Error, sync_playwright

        endpoint = opened.get("ws") or opened.get("http") if isinstance(opened, dict) else None
        if not endpoint:
            raise BitBrowserError("无头窗口未返回调试地址")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(endpoint, timeout=15000)
                if not browser.contexts:
                    raise BitBrowserError("无头窗口缺少原账号浏览器上下文")
                context = browser.contexts[0]
                page = context.new_page()
                page.goto("https://www.goofish.com/", wait_until="domcontentloaded", timeout=30000)
                for _ in range(30):
                    records = self._first_party_cookies(self._parse_cookies(
                        context.cookies(["https://www.goofish.com"])))
                    if self._fresh(records) and not self._conflicting_cookies(records):
                        return records
                    page.wait_for_timeout(1000)
        except Error as exc:
            raise BitBrowserError("无头窗口加载闲鱼失败") from exc
        raise BitBrowserError("无头刷新后 Cookie 仍过期或未登录，请在原窗口完成登录验证")

    def cookies(self, profile_id: str) -> list[dict[str, Any]]:
        # 已打开窗口优先读取实时凭据，避免同步快照覆盖浏览器中的新令牌。
        live_error = None
        try:
            data = self._post("/browser/cookies/get", {"browserId": profile_id})
            records = self._first_party_cookies(self._parse_cookies(data or []))
        except BitBrowserError as exc:
            # 单个实时接口失败不等于原窗口退出登录，继续核对运行窗口。
            live_error, records = exc, []
        if self._fresh(records) and not self._conflicting_cookies(records):
            return records
        pids = self._post("/browser/pids", {"ids": [profile_id]}) or {}
        if not isinstance(pids, dict):
            raise BitBrowserError("比特浏览器窗口状态返回格式不正确")
        if pids.get(profile_id):
            # 空值、过期、缺字段或冲突均整组读取，不拼接不同来源的签名字段。
            records = self._parse_cookies(self._first_party_cookies(
                self._read_running_context_cookies(profile_id)))
            if self._conflicting_cookies(records):
                raise BitBrowserError("实时浏览器 Cookie 仍存在同名冲突，拒绝猜测凭据")
            if not self._fresh(records):
                raise BitBrowserError("原窗口尚未取得有效 Cookie，不能据此判断浏览器已退出登录")
            return records
        if live_error is not None:
            raise live_error
        detail = self._post("/browser/detail", {"id": profile_id}) or {}
        data = detail.get("cookie", "") if isinstance(detail, dict) else ""
        snapshot = self._first_party_cookies(self._parse_cookies(data or []))
        if self._fresh(snapshot) and not self._conflicting_cookies(snapshot):
            return snapshot
        try:
            opened = self.open_headless(profile_id)
            return self._parse_cookies(self._load_headless_page(opened))
        finally:
            # 打开请求即使超时也可能已创建窗口，因此也必须进入清理。
            self.close(profile_id)

    @staticmethod
    def _first_party_cookies(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """当前闲鱼站点分区优先；不混入其他顶层站点的分区凭据。"""
        current = [r for r in records if r.get("partitionKey") == "https://goofish.com"]
        names = {r["name"] for r in current}
        return [r for r in records if r in current or
                (not r.get("partitionKey") and r["name"] not in names)]

    @staticmethod
    def _conflicting_cookies(records: list[dict[str, Any]]) -> bool:
        values: dict[str, str] = {}
        for record in records:
            name, value = record["name"], record["value"]
            if name in values and values[name] != value:
                return True
            values[name] = value
        return False

    def _read_running_context_cookies(self, profile_id: str) -> list[dict[str, Any]]:
        """只读取已运行的绑定窗口；不导航、不关闭用户窗口。"""
        from playwright.sync_api import Error, sync_playwright

        pids = self._post("/browser/pids", {"ids": [profile_id]}) or {}
        if not isinstance(pids, dict) or not pids.get(profile_id):
            raise BitBrowserError("读取期间绑定窗口已停止或状态不可确认，请检查原窗口")
        opened = self._post("/browser/open", {"id": profile_id, "queue": True,
                                               "ignoreDefaultUrls": True})
        endpoint = opened.get("ws") or opened.get("http") if isinstance(opened, dict) else None
        if not endpoint:
            raise BitBrowserError("已运行窗口未返回调试地址")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(endpoint, timeout=15000)
                if len(browser.contexts) != 1:
                    raise BitBrowserError("绑定窗口浏览器上下文不唯一")
                context = browser.contexts[0]
                for attempt in range(5):
                    records = self._first_party_cookies(self._parse_cookies(
                        context.cookies(["https://www.goofish.com"])))
                    if self._fresh(records) and not self._conflicting_cookies(records):
                        return records
                    if attempt < 4:
                        time.sleep(1)
        except Error as exc:
            raise BitBrowserError("读取绑定窗口实时 Cookie 失败") from exc
        if self._conflicting_cookies(records):
            raise BitBrowserError("实时浏览器 Cookie 仍存在同名冲突，拒绝猜测凭据")
        raise BitBrowserError(
            "原窗口尚未取得有效 Cookie（字段缺失或令牌过期），不能据此判断浏览器已退出登录；"
            "请检查原窗口闲鱼页面是否加载完成或需要验证")

    @staticmethod
    def _parse_cookies(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise BitBrowserError("比特浏览器 Cookie 返回不是有效 JSON") from exc
        if isinstance(data, dict):
            data = data.get("cookies", data.get("list", []))
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise BitBrowserError("比特浏览器 Cookie 返回格式不正确")
        records = []
        for item in data:
            name, value = item.get("name"), item.get("value")
            if isinstance(name, str) and name and isinstance(value, str):
                records.append({"name": name, "value": value,
                                "domain": item.get("domain", ".goofish.com"),
                                "path": item.get("path", "/"),
                                **({"partitionKey": item["partitionKey"]}
                                   if item.get("partitionKey") else {})})
        return records

    def cookies_for_account(self, group_name: str, account_name: str,
                            expected_uid: str = "") -> list[dict[str, Any]]:
        profile = self.profile_for_account(group_name, account_name)
        records = self.cookies(profile.profile_id)
        self.validate_records(records, account_name, expected_uid)
        return records

    def profile_for_account(self, group_name: str, account_name: str,
                            profile_id: str = "") -> BitBrowserProfile:
        profiles = self.profiles(group_name)
        matches = [profile for profile in profiles if
                   (profile.profile_id == profile_id if profile_id else profile.name == account_name)]
        if len(matches) != 1:
            raise BitBrowserError(f"分组 {group_name} 中账号名称匹配不唯一：{account_name}")
        return matches[0]

    @staticmethod
    def validate_records(records: list[dict[str, Any]], account_name: str, expected_uid: str) -> None:
        uid = next((item["value"] for item in records if item["name"] == "unb"), "")
        if not uid or (expected_uid and uid != expected_uid):
            raise BitBrowserError(f"比特浏览器账号 {account_name} Cookie UID 与绑定不符")
        required = {item["name"] for item in records if item.get("value")}
        if not {"unb", "_m_h5_tk", "cookie2"} <= required:
            raise BitBrowserError(f"比特浏览器账号 {account_name} Cookie 缺少闲鱼关键字段")

    @staticmethod
    def parse_settings(detail: dict) -> tuple[NetworkProfile, ClientProfile]:
        """只解析明确的自定义代理；动态提取、全局代理不能拿旧地址猜测。"""
        if not isinstance(detail, dict):
            raise BitBrowserError("比特窗口详情格式无效")
        if detail.get("proxyMethod") != 2 or detail.get("isGlobalProxyInfo"):
            raise BitBrowserError("仅支持比特自定义代理；动态提取或全局代理尚不能可靠读取")
        kind = detail.get("proxyType")
        if kind == "noproxy":
            network = NetworkProfile()
        else:
            if kind not in {"http", "https", "socks5"}:
                raise BitBrowserError("比特窗口代理类型缺失或不支持")
            host, port = detail.get("host"), detail.get("port")
            if (not isinstance(host, str) or not host
                    or any(char in host for char in "/@?#") or any(c.isspace() for c in host)):
                raise BitBrowserError("比特窗口代理主机无效")
            if isinstance(port, str) and port.isdecimal():
                port = int(port)
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise BitBrowserError("比特窗口代理端口无效")
            username, password = detail.get("proxyUserName") or "", detail.get("proxyPassword") or ""
            if not isinstance(username, str) or not isinstance(password, str) or (password and not username):
                raise BitBrowserError("比特窗口代理认证字段无效")
            auth = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username else ""
            host = f"[{host}]" if ":" in host and not host.startswith("[") else host
            network = NetworkProfile(mode="proxy", proxy_url=f"{kind}://{auth}{host}:{port}")
        fingerprint = detail.get("browserFingerPrint")
        if not isinstance(fingerprint, dict) or not fingerprint.get("userAgent"):
            raise BitBrowserError("比特窗口缺少明确的 User-Agent，请先保存窗口指纹配置")
        return network, ClientProfile(user_agent=fingerprint["userAgent"])

    def settings_for_profile(self, profile_id: str) -> tuple[NetworkProfile, ClientProfile]:
        detail = self._post("/browser/detail", {"id": profile_id})
        if isinstance(detail, dict) and detail.get("id", profile_id) != profile_id:
            raise BitBrowserError("比特窗口详情身份不匹配")
        return self.parse_settings(detail)

    def account_context(self, group_name: str, account_name: str, expected_uid: str,
                        profile_id: str = ""):
        """同一窗口提供凭据、代理与 UA；由调用方固定到账号会话。"""
        profile = self.profile_for_account(group_name, account_name, profile_id)
        records = self.cookies(profile.profile_id)
        self.validate_records(records, account_name, expected_uid)
        # 无头刷新可能更新窗口 UA，读取完成后的配置再固定到会话。
        network, client = self.settings_for_profile(profile.profile_id)
        return records, network, client
