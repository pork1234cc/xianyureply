"""账号目录、配置和直连隔离的离线测试。"""

from pathlib import Path

import pytest
import yaml

from goofish_bridge.config import AccountPaths, Config
from goofish_bridge.network import NetworkProfile


def write_config(tmp_path, mutate=lambda raw: None):
    raw = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def test_directories_are_separate(tmp_path):
    cfg = Config.load(write_config(tmp_path))
    cfg.initialize_directories()
    assert len({AccountPaths(tmp_path, key).directory for key in ("A1", "A2", "A3")}) == 3
    assert AccountPaths(tmp_path, "A1").directory.is_dir()


@pytest.mark.parametrize("key", ["../A1", "A0", "A01", "", None])
def test_invalid_account_key(tmp_path, key):
    with pytest.raises(ValueError):
        AccountPaths(tmp_path, key)


def test_shared_account_directory_rejected(tmp_path):
    path = write_config(tmp_path, lambda raw: raw["accounts"][1].update(data_dir="./accounts/A1"))
    with pytest.raises(ValueError):
        Config.load(path)


def test_numeric_uid_rejected(tmp_path):
    path = write_config(tmp_path, lambda raw: raw["accounts"][0].update(expected_uid=123))
    with pytest.raises(ValueError):
        Config.load(path)


def test_dynamic_account_count(tmp_path):
    def expand(raw):
        for number in (4, 5):
            raw["accounts"].append({"key": f"A{number}", "name": str(number),
                                    "data_dir": f"accounts/A{number}"})
    config = Config.load(write_config(tmp_path, expand))
    config.initialize_directories()
    assert AccountPaths(tmp_path, "A5").directory.is_dir()


def test_direct_http_ignores_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    with NetworkProfile().http() as session:
        options = session.merge_environment_settings("https://example.com", {}, False, True, None)
        assert options["proxies"] == {}
        assert not session.trust_env


def test_proxy_fails_closed():
    with pytest.raises(ValueError):
        NetworkProfile(mode="proxy")


def test_websocket_explicit_direct(monkeypatch):
    seen = {}
    monkeypatch.setattr("goofish_bridge.network.websockets.connect", lambda uri, **kw: seen.update(kw))
    NetworkProfile().websocket("wss://example.com", proxy="http://wrong")
    assert seen["proxy"] is None


def test_account_proxy_applies_to_http_and_websocket(monkeypatch):
    proxy = "http://user:secret@127.0.0.1:18080"
    monkeypatch.setenv("ACCOUNT_PROXY", proxy)
    monkeypatch.setenv("HTTPS_PROXY", "http://wrong:9999")
    profile = NetworkProfile(mode="proxy", proxy_url_env="ACCOUNT_PROXY")
    with profile.http() as session:
        options = session.merge_environment_settings("https://example.com", {}, False, True, None)
        assert options["proxies"] == {"http": proxy, "https": proxy}
        assert not session.trust_env
    seen = []
    monkeypatch.setattr("goofish_bridge.network.websockets.connect",
                        lambda uri, **kw: seen.append(kw["proxy"]))
    profile.websocket("wss://example.com")
    monkeypatch.setenv("ACCOUNT_PROXY", "http://changed:9999")
    profile.websocket("wss://example.com")
    assert seen == [proxy, proxy]
    assert "secret" not in repr(profile)


@pytest.mark.parametrize("value", ["", "localhost:8080", "socks4://localhost:1080",
                                  "http://", "http://localhost:bad", "http://localhost/path"])
def test_invalid_proxy_never_falls_back_to_direct(monkeypatch, value):
    monkeypatch.setenv("ACCOUNT_PROXY", value)
    with pytest.raises(ValueError):
        NetworkProfile(mode="proxy", proxy_url_env="ACCOUNT_PROXY")


def test_proxy_browser_options(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PROXY", "http://user:p%40ss@localhost:8080")
    options = NetworkProfile(mode="proxy", proxy_url_env="ACCOUNT_PROXY").browser_options()
    assert "--no-proxy-server" not in options.get("args", [])
    assert options["proxy"] == {"server": "http://localhost:8080", "username": "user", "password": "p@ss"}


def test_socks5_uses_remote_dns_in_both_clients(monkeypatch):
    profile = NetworkProfile(mode="proxy", proxy_url="socks5://u:p@localhost:1080")
    assert profile.http().proxies["https"] == "socks5h://u:p@localhost:1080"
    seen = {}
    monkeypatch.setattr("goofish_bridge.network.websockets.connect", lambda uri, **kw: seen.update(kw))
    profile.websocket("wss://example.com")
    assert seen["proxy"] == "socks5h://u:p@localhost:1080"
    assert "u:p" not in repr(profile)


def test_authenticated_socks_qr_is_explicitly_rejected():
    profile = NetworkProfile(mode="proxy", proxy_url="socks5://u:p@localhost:1080")
    with pytest.raises(ValueError, match="比特"):
        profile.browser_options()
