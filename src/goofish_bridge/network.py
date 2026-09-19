"""按账号固定连接出口，不修改电脑或其他进程的代理配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

import requests
import websockets


@dataclass(frozen=True)
class NetworkProfile:
    mode: str = "direct"
    proxy_url_env: str | None = None
    proxy_url: str | None = field(default=None, repr=False)
    _proxy_url: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if self.mode == "direct":
            if self.proxy_url_env or self.proxy_url:
                raise ValueError("直连配置不能包含代理变量")
            return
        if self.mode != "proxy":
            raise ValueError("网络模式只能是 direct 或 proxy")
        if self.proxy_url is not None and self.proxy_url_env:
            raise ValueError("不能同时指定代理地址和代理环境变量")
        if self.proxy_url is None and (not isinstance(self.proxy_url_env, str) or not self.proxy_url_env):
            raise ValueError("代理模式必须指定 proxy_url_env")
        value = self.proxy_url if self.proxy_url is not None else os.getenv(self.proxy_url_env, "")
        if not isinstance(value, str):
            raise ValueError("代理地址必须是字符串")
        value = value.strip()
        if value.startswith("socks5://"):
            value = "socks5h://" + value[len("socks5://"):]
        try:
            parts = urlsplit(value)
            valid = (parts.scheme in {"http", "https", "socks5h"} and parts.hostname
                     and parts.path in {"", "/"} and not parts.query and not parts.fragment
                     and not any(char.isspace() for char in value))
            port = parts.port
            if not valid or port == 0:
                raise ValueError
        except ValueError:
            # 不把含用户名、密码的 URL 写进异常。
            raise ValueError("代理地址为空或格式无效，仅支持 HTTP/HTTPS/SOCKS5 代理") from None
        object.__setattr__(self, "_proxy_url", value)

    def http(self) -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        session.proxies.clear()
        if self._proxy_url:
            session.proxies.update(http=self._proxy_url, https=self._proxy_url)
        return session

    def websocket(self, uri: str, **kwargs):
        kwargs["proxy"] = self._proxy_url
        if self._proxy_url:
            parts = urlsplit(self._proxy_url)
            if "%" in (parts.username or "") + (parts.password or ""):
                from goofish_bridge.proxy_auth import AuthenticatedProxyConnect

                return AuthenticatedProxyConnect(uri, **kwargs)
        return websockets.connect(uri, **kwargs)

    def browser_options(self) -> dict:
        options = {"channel": "chrome", "headless": False}
        if self._proxy_url is None:
            return {**options, "args": ["--no-proxy-server"]}
        parts = urlsplit(self._proxy_url)
        if parts.scheme == "socks5h" and parts.username is not None:
            raise ValueError("独立扫码 Chrome 不支持带认证的 SOCKS5，请在原比特窗口登录")
        scheme = "socks5" if parts.scheme == "socks5h" else parts.scheme
        proxy = {"server": f"{scheme}://{parts.netloc.rsplit('@', 1)[-1]}"}
        if parts.username is not None:
            proxy.update(username=unquote(parts.username), password=unquote(parts.password or ""))
        return {**options, "proxy": proxy}
