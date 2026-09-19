"""离线验证账号出口、客户端声明和设备缓存，不接触真实凭据。"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import requests

from goofish_bridge import goofish_adapter as adapter
from goofish_bridge.account import load_session, save_login
from goofish_bridge.config import AccountPaths, Config
from goofish_cli.core import mtop, ws
from goofish_cli.core import session as session_module
from tests.test_bridge_config import write_config


def account_session(tmp_path, monkeypatch, key="A1"):
    config = Config.load(write_config(tmp_path))
    config.initialize_directories()
    config.account(key)["network"] = {"mode": "proxy", "proxy_url_env": f"{key}_PROXY"}
    monkeypatch.setenv(f"{key}_PROXY", f"http://localhost:{18080 + int(key[-1])}")
    paths = AccountPaths(tmp_path, key)
    uid = f"user-{key}"
    save_login(config, paths, [{"name": "unb", "value": uid},
                              {"name": "_m_h5_tk", "value": "test_token"}], uid)
    paths.file("device.json").write_text(json.dumps({"unb": uid, "device_id": f"stable-{key}"}), encoding="utf-8")
    # 若错误地回退到全局缓存，不得读写用户目录。
    monkeypatch.setattr(session_module, "DEVICE_CACHE_PATH", tmp_path / "unexpected-device.json")
    monkeypatch.setattr(session_module, "generate_device_id", lambda _: "unexpected-new-device")
    return config, paths, load_session(config, paths)


def test_each_account_preserves_existing_device_and_proxy(tmp_path, monkeypatch):
    sessions = []
    for key in ("A1", "A2"):
        config, paths, session = account_session(tmp_path, monkeypatch, key)
        sessions.append(session)
        assert session.device_id == f"stable-{key}"
        assert load_session(config, paths).device_id == session.device_id
        assert session.http.proxies["https"] == f"http://localhost:{18080 + int(key[-1])}"
    assert sessions[0].http is not sessions[1].http
    assert not (tmp_path / "unexpected-device.json").exists()


@pytest.mark.asyncio
async def test_real_account_connector_survives_reconnect(tmp_path, monkeypatch):
    _, _, session = account_session(tmp_path, monkeypatch)
    seen = []

    @asynccontextmanager
    async def connect(uri, **kwargs):
        seen.append(kwargs)
        yield SimpleNamespace()

    async def register(*args):
        return {"reg": "test"}

    async def ready(*args):
        return None

    async def wait(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr("goofish_bridge.network.websockets.connect", connect)
    monkeypatch.setattr(adapter.guard, "check", lambda: None)
    monkeypatch.setattr(adapter, "get_access_token", lambda _: "test-token")
    monkeypatch.setattr(adapter, "register", register)
    monkeypatch.setattr(adapter, "heartbeat_loop", wait)
    monkeypatch.setattr(adapter.ProbeConnection, "wait_ready", ready)
    monkeypatch.setattr(adapter.ProbeConnection, "receive", wait)
    for _ in range(2):
        async with adapter.connection(session, None, "test"):
            pass
    assert [item["proxy"] for item in seen] == ["http://localhost:18081"] * 2


@pytest.mark.asyncio
async def test_http_ws_and_registration_share_client_identity(monkeypatch):
    session = session_module.Session(requests.Session(), "test-user", "", "stable-device")
    session.http.cookies.update({"_m_h5_tk": "test_token"})
    posted = {}
    monkeypatch.setattr(mtop, "generate_sign", lambda *args: "test-sign")
    monkeypatch.setattr(session.http, "post", lambda *args, **kw:
                        (posted.update(kw) or SimpleNamespace(json=lambda: {"ret": ["SUCCESS"]})))
    mtop.call(session, "test.api", {})
    handshake = ws._handshake_headers(session)
    assert posted["headers"]["user-agent"] == handshake["User-Agent"]
    assert posted["headers"]["sec-ch-ua-platform"] == '"Windows"'
    frames = []

    async def send(raw):
        frames.append(json.loads(raw))

    await ws.register(SimpleNamespace(send=send), session, "test-token")
    assert frames[0]["headers"]["ua"].startswith(handshake["User-Agent"])
    assert frames[0]["headers"]["did"] == "stable-device"


def test_account_can_use_explicit_browser_user_agent(tmp_path, monkeypatch):
    config, paths, _ = account_session(tmp_path, monkeypatch)
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")
    config.account("A1")["client"] = {"user_agent": ua}
    session = load_session(config, paths)
    assert ws._handshake_headers(session)["User-Agent"] == ua
    assert session.client.hints["sec-ch-ua-platform"] == '"macOS"'
    assert 'v="146"' in session.client.hints["sec-ch-ua"]
    assert "Browser(Chrome/146.0.0.0)" in session.client.im_user_agent
    assert session.device_id == "stable-A1"


@pytest.mark.parametrize("ua", [None, "", "Chrome/146.0.0.0", "invalid\r\nheader: value",
                                "Mozilla/5.0 (Linux; Android 12) Chrome/146.0.0.0 Mobile"])
def test_invalid_client_identity_is_rejected(ua):
    from goofish_cli.core.client_profile import ClientProfile

    with pytest.raises(ValueError):
        ClientProfile(user_agent=ua)


def test_qr_login_receives_account_network(tmp_path, monkeypatch):
    from goofish_bridge import __main__ as cli

    config, paths, _ = account_session(tmp_path, monkeypatch)
    seen = {}

    async def scan(account_paths, timeout, **kwargs):
        seen.update(kwargs)
        return [{"name": "unb", "value": "user-A1"}, {"name": "_m_h5_tk", "value": "test_token"}]

    monkeypatch.setattr("goofish_bridge.account.initialize_account", lambda *args: paths)
    monkeypatch.setattr("goofish_bridge.account.scan_login", scan)
    monkeypatch.setattr("builtins.input", lambda _: "user-A1")
    cli.login(config, SimpleNamespace(account="A1", qr=True, timeout=10))
    assert seen["network"].http().proxies["https"] == "http://localhost:18081"


def test_feishu_rejects_unsupported_proxy_instead_of_ignoring(tmp_path, monkeypatch):
    from goofish_bridge.feishu_adapter import credentials

    config = Config.load(write_config(tmp_path))
    config.raw["feishu"]["network"] = {"mode": "proxy", "proxy_url_env": "FEISHU_PROXY"}
    monkeypatch.setenv("FEISHU_PROXY", "http://localhost:18080")
    for field in ("app_id_env", "app_secret_env", "allowed_open_id_env"):
        monkeypatch.setenv(config.raw["feishu"][field], "test")
    with pytest.raises(ValueError, match="飞书目前仅支持直连"):
        credentials(config)


def test_bitbrowser_context_overrides_manual_network_and_client(tmp_path, monkeypatch):
    from goofish_bridge.bitbrowser_cookie import BitBrowserClient, BitBrowserError
    from goofish_bridge.network import NetworkProfile
    from goofish_cli.core.client_profile import ClientProfile

    config, paths, original = account_session(tmp_path, monkeypatch)
    config.raw["bitbrowser"]["enabled"] = True
    network = NetworkProfile(mode="proxy", proxy_url="socks5://localhost:1080")
    client = ClientProfile()
    seen = []

    def context(self, group, name, expected):
        seen.append(expected)
        return original.cookie_records, network, client

    monkeypatch.setattr(BitBrowserClient, "account_context", context)
    session = load_session(config, paths)
    assert session.http.proxies["https"] == "socks5h://localhost:1080"
    assert session.websocket_connect.__self__ is network
    assert session.client is client
    assert session.device_id == "stable-A1"
    assert seen == ["user-A1"]

    def fail(*args):
        raise BitBrowserError("读取失败")

    monkeypatch.setattr(BitBrowserClient, "account_context", fail)
    with pytest.raises(BitBrowserError):
        load_session(config, paths)
