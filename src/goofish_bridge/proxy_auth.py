"""适配 websockets 15 的代理认证 URL 解码，保持原 TLS 验证。"""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import replace
from urllib.parse import unquote

from websockets.asyncio.client import connect, connect_http_proxy, connect_socks_proxy
from websockets.uri import parse_proxy, parse_uri


class AuthenticatedProxyConnect(connect):
    """仅在 URL 编码认证字段时使用；不修改第三方模块的全局函数。"""

    async def create_connection(self):
        proxy = parse_proxy(self.proxy)
        proxy = replace(proxy, username=unquote(proxy.username) if proxy.username is not None else None,
                        password=unquote(proxy.password) if proxy.password is not None else None)
        target = parse_uri(self.uri)
        loop = asyncio.get_running_loop()
        kwargs = self.connection_kwargs.copy()
        if any(key in kwargs for key in ("sock", "unix", "host", "port")):
            raise ValueError("账号代理连接不允许覆盖底层出口")
        if target.secure:
            kwargs.setdefault("ssl", True)
            kwargs.setdefault("server_hostname", target.host)
            if not kwargs["ssl"]:
                raise ValueError("WSS 不允许禁用 TLS")
        elif kwargs.get("ssl") is not None:
            raise ValueError("WS 不能包含 TLS 参数")

        def factory():
            return self.protocol_factory(target)

        if proxy.scheme.startswith("socks"):
            sock = await connect_socks_proxy(proxy, target, local_addr=kwargs.pop("local_addr", None))
            try:
                _, connection = await loop.create_connection(factory, sock=sock, **kwargs)
            except BaseException:
                sock.close()
                raise
            return connection

        proxy_kwargs, target_kwargs = {}, {}
        for key, value in kwargs.items():
            if key.startswith("ssl") or key == "server_hostname":
                target_kwargs[key] = value
            else:
                proxy_kwargs[key.removeprefix("proxy_")] = value
        if proxy.scheme == "https":
            proxy_kwargs.setdefault("ssl", True)
            if not proxy_kwargs["ssl"]:
                raise ValueError("HTTPS 代理不允许禁用 TLS")
        elif proxy_kwargs.get("ssl") is not None:
            raise ValueError("HTTP 代理不能包含 TLS 参数")
        transport = await connect_http_proxy(proxy, target, user_agent_header=self.user_agent_header,
                                             **proxy_kwargs)
        try:
            connection = factory()
            transport.set_protocol(connection)
            context = target_kwargs.pop("ssl", None)
            if context is True:
                context = ssl.create_default_context()
            if context is not None:
                upgraded = await loop.start_tls(transport, connection, context, **target_kwargs)
                if upgraded is None:
                    raise ConnectionError("代理 TLS 通道已关闭")
                transport = upgraded
            connection.connection_made(transport)
            return connection
        except BaseException:
            transport.close()
            raise
