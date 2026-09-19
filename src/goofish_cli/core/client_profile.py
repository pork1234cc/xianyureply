"""协议客户端声明；不代表浏览器指纹或浏览器本地存储。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class ClientProfile:
    user_agent: str = DEFAULT_USER_AGENT
    platform: str = field(init=False)
    chrome_version: str = field(init=False)
    os_version: str = field(init=False)

    def __post_init__(self):
        ua = self.user_agent
        if (not isinstance(ua, str) or not ua.isascii()
                or any(ord(char) < 32 or ord(char) == 127 for char in ua)):
            raise ValueError("user_agent 必须是有效的桌面 Chrome UA")
        chrome = re.search(r"Chrome/(\d+\.\d+\.\d+\.\d+)", ua)
        windows = re.search(r"Windows NT ([\d.]+)", ua)
        mac = re.search(r"Mac OS X ([\d_]+)", ua)
        if not chrome or any(marker in ua for marker in ("Mobile", "Android", "Edg/", "OPR/")):
            raise ValueError("目前仅支持 Windows、macOS 或 Linux 桌面 Chrome UA")
        if windows:
            platform, version = "Windows", windows[1]
        elif mac:
            platform, version = "macOS", mac[1].replace("_", ".")
        elif "Linux" in ua:
            platform, version = "Linux", ""
        else:
            raise ValueError("无法识别 user_agent 中的桌面操作系统")
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "os_version", version)
        object.__setattr__(self, "chrome_version", chrome[1])

    @property
    def hints(self) -> dict[str, str]:
        major = self.chrome_version.split(".")[0]
        return {
            "sec-ch-ua": f'"Chromium";v="{major}", "Not-A.Brand";v="24", "Google Chrome";v="{major}"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{self.platform}"',
        }

    @property
    def im_user_agent(self) -> str:
        os_name = f"{self.platform}/{self.os_version}" if self.os_version else self.platform
        return (f"{self.user_agent} DingTalk(2.1.5) OS({os_name}) "
                f"Browser(Chrome/{self.chrome_version}) DingWeb/2.1.5 IMPaaS DingWeb/2.1.5")
