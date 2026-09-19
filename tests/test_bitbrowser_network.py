"""比特窗口代理和客户端声明的解析，不读取实际窗口。"""

from types import SimpleNamespace

import pytest

from goofish_bridge.bitbrowser_cookie import BitBrowserClient, BitBrowserError
from goofish_cli.core.client_profile import DEFAULT_USER_AGENT


def detail(kind="socks5"):
    return {"proxyMethod": 2, "proxyType": kind, "host": "localhost", "port": 1080,
            "proxyUserName": "u@name", "proxyPassword": "p:/?#",
            "browserFingerPrint": {"userAgent": DEFAULT_USER_AGENT}}


@pytest.mark.parametrize("kind", ["http", "https", "socks5"])
def test_window_proxy_types_credentials_and_ua(kind):
    network, client = BitBrowserClient.parse_settings(detail(kind))
    scheme = "socks5h" if kind == "socks5" else kind
    assert network.http().proxies["https"] == f"{scheme}://u%40name:p%3A%2F%3F%23@localhost:1080"
    assert client.user_agent == DEFAULT_USER_AGENT


def test_only_explicit_noproxy_allows_direct():
    data = detail("noproxy")
    assert BitBrowserClient.parse_settings(data)[0].mode == "direct"


@pytest.mark.parametrize("changes", [{"proxyType": None}, {"host": ""}, {"port": 0},
                                     {"proxyMethod": 3}, {"isGlobalProxyInfo": True},
                                     {"proxyType": "ssh"}, {"browserFingerPrint": {}},
                                     {"host": "localhost/path"}])
def test_missing_or_unsupported_window_settings_fail_closed(changes):
    with pytest.raises((ValueError, BitBrowserError)):
        BitBrowserClient.parse_settings({**detail(), **changes})


def test_context_uses_same_window_and_checks_uid(monkeypatch):
    api = BitBrowserClient("http://localhost")
    monkeypatch.setattr(api, "profiles", lambda _: [SimpleNamespace(name="A1", profile_id="p1")])
    monkeypatch.setattr(api, "cookies", lambda pid: [{"name": "unb", "value": "wrong"},
                         {"name": "_m_h5_tk", "value": "test"}, {"name": "cookie2", "value": "test"}])
    monkeypatch.setattr(api, "_post", lambda path, body: detail())
    with pytest.raises(BitBrowserError, match="UID"):
        api.account_context("闲鱼", "A1", "expected")
