"""集中创建直连连接，不修改电脑或其他进程的代理配置。"""

from __future__ import annotations

from dataclasses import dataclass

import requests
import websockets


@dataclass(frozen=True)
class NetworkProfile:
    mode: str = "direct"
    proxy_url_env: str | None = None

    def __post_init__(self):
        if self.mode != "direct":
            raise NotImplementedError("首版只支持 direct，代理功能尚未实现")
        if self.proxy_url_env:
            raise ValueError("直连配置不能包含代理变量")

    def http(self) -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        session.proxies.clear()
        return session

    def websocket(self, uri: str, **kwargs):
        kwargs["proxy"] = None
        return websockets.connect(uri, **kwargs)

    def browser_options(self) -> dict:
        return {"channel": "chrome", "headless": False, "args": ["--no-proxy-server"]}
