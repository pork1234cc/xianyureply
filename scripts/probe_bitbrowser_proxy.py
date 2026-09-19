"""读取指定比特窗口，匿名验证代理；不登录 IM、不发送客户消息。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from goofish_bridge.bitbrowser_cookie import BitBrowserClient
from goofish_bridge.config import Config
from goofish_bridge.network import NetworkProfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


async def probe(network, client):
    # 无 Cookie、无 Token、不发送 /reg，避免踢掉正在运行的账号监听。
    async with network.websocket(
        "wss://wss-goofish.dingtalk.com/", origin="https://www.goofish.com",
        user_agent_header=client.user_agent, open_timeout=15, close_timeout=3,
        ping_interval=None,
    ):
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", choices=["A1", "A2", "A3"], required=True)
    args = parser.parse_args()
    config = Config.load(Path("config.yaml"))
    settings = config.raw["bitbrowser"]
    api = BitBrowserClient(settings["api_url"])
    result = {"账号": args.account, "匿名测试": True}
    try:
        profile = api.profile_for_account(settings["group_name"], config.account(args.account)["name"])
        network, client = api.settings_for_profile(profile.profile_id)
        result["窗口"] = profile.name
        result["模式"] = network.mode
        result["UA平台"] = client.platform
        result["Chrome版本"] = client.chrome_version
        for label, url in [("闲鱼首页", "https://www.goofish.com/"),
                           ("闲鱼API域名", "https://h5api.m.goofish.com/"),
                           ("HTTPS对照站点", "https://www.baidu.com/")]:
            for mode, transport in [("代理", network), ("直连", NetworkProfile())]:
                try:
                    with transport.http() as http:
                        response = http.get(url, timeout=12, allow_redirects=False)
                        result[f"{label}/{mode}"] = {"TLS通过": True, "状态码": response.status_code}
                except Exception as exc:
                    result[f"{label}/{mode}"] = {"TLS通过": False, "错误类型": type(exc).__name__}
        try:
            result["闲鱼WSS握手"] = asyncio.run(probe(network, client))
        except Exception as exc:
            result["闲鱼WSS握手"] = False
            result["WSS错误类型"] = type(exc).__name__
        result["通过"] = result["闲鱼API域名/代理"]["TLS通过"] and result["闲鱼WSS握手"]
    except Exception as exc:
        # 原始异常可能包含代理地址与密码，只打印类型。
        result.update(通过=False, 错误类型=type(exc).__name__)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["通过"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
