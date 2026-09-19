"""从比特浏览器指定分组读取闲鱼账号 Cookie。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests


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

    def profiles(self, group_name: str) -> list[BitBrowserProfile]:
        groups = self._post("/group/list", {"page": 0, "pageSize": 100}) or {}
        group = next((item for item in groups.get("list", [])
                      if item.get("groupName") == group_name), None)
        if not group:
            raise BitBrowserError(f"找不到比特浏览器分组：{group_name}")
        profiles = self._post("/browser/list", {"page": 0, "pageSize": 100,
                                                  "groupId": group["id"]}) or {}
        result = []
        for item in profiles.get("list", []):
            profile_id = item.get("id")
            if profile_id and item.get("platform", "").find("goofish.com") >= 0:
                result.append(BitBrowserProfile(profile_id, item.get("name", ""), group_name))
        return result

    def open_headless(self, profile_id: str) -> None:
        """以无头模式打开窗口，使实时 Cookie 接口可用。"""
        self._post("/browser/open", {"id": profile_id, "args": ["--headless"],
                                      "queue": True, "ignoreDefaultUrls": True})

    def close(self, profile_id: str) -> None:
        """关闭本模块临时打开的窗口。"""
        self._post("/browser/close", {"id": profile_id})

    def cookies(self, profile_id: str) -> list[dict[str, Any]]:
        # 已打开窗口优先读取实时凭据，避免同步快照覆盖浏览器中的新令牌。
        data = self._post("/browser/cookies/get", {"browserId": profile_id})
        if not data:
            pids = self._post("/browser/pids", {"ids": [profile_id]}) or {}
            if not isinstance(pids, dict):
                raise BitBrowserError("比特浏览器窗口状态返回格式不正确")
            if pids.get(profile_id):
                raise BitBrowserError("已打开的比特窗口尚未取得 Cookie，请等待闲鱼页面加载后重试")
            detail = self._post("/browser/detail", {"id": profile_id}) or {}
            data = detail.get("cookie", "") if isinstance(detail, dict) else ""
        if not data:
            self.open_headless(profile_id)
            try:
                data = self._post("/browser/cookies/get", {"browserId": profile_id})
            finally:
                self.close(profile_id)
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
                                "path": item.get("path", "/")})
        return records

    def cookies_for_account(self, group_name: str, account_name: str,
                            expected_uid: str = "") -> list[dict[str, Any]]:
        profiles = self.profiles(group_name)
        matches = [profile for profile in profiles if profile.name == account_name]
        if len(matches) != 1:
            raise BitBrowserError(f"分组 {group_name} 中账号名称匹配不唯一：{account_name}")
        records = self.cookies(matches[0].profile_id)
        uid = next((item["value"] for item in records if item["name"] == "unb"), "")
        if not uid or (expected_uid and uid != expected_uid):
            raise BitBrowserError(f"比特浏览器账号 {account_name} Cookie UID 与绑定不符")
        required = {item["name"] for item in records}
        if not {"unb", "_m_h5_tk", "cookie2"} <= required:
            raise BitBrowserError(f"比特浏览器账号 {account_name} Cookie 缺少闲鱼关键字段")
        return records
