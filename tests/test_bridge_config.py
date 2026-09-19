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


@pytest.mark.parametrize("key", ["../A1", "A4", "", None])
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


def test_direct_http_ignores_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    with NetworkProfile().http() as session:
        options = session.merge_environment_settings("https://example.com", {}, False, True, None)
        assert options["proxies"] == {}
        assert not session.trust_env


def test_proxy_fails_closed():
    with pytest.raises(NotImplementedError):
        NetworkProfile(mode="proxy")


def test_websocket_explicit_direct(monkeypatch):
    seen = {}
    monkeypatch.setattr("goofish_bridge.network.websockets.connect", lambda uri, **kw: seen.update(kw))
    NetworkProfile().websocket("wss://example.com", proxy="http://wrong")
    assert seen["proxy"] is None
