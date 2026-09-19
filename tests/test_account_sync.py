"""窗口发现与本地身份去重回归，不访问真实浏览器。"""

from unittest.mock import Mock

import pytest

from goofish_bridge.bitbrowser_cookie import BitBrowserClient, BitBrowserError, BitBrowserProfile
from goofish_bridge.config import AccountPaths, Config
from tests.test_bridge_config import write_config


def setup_sync(tmp_path):
    path = write_config(tmp_path, lambda raw: raw.update(accounts=[], bitbrowser={"enabled": True}))
    api = Mock(spec=BitBrowserClient)
    api.profiles.return_value = [BitBrowserProfile(f"p{i}", f"窗口{i}", "闲鱼") for i in range(5)]
    api.cookies.side_effect = lambda pid: [
        {"name": name, "value": value} for name, value in
        [("unb", pid), ("cookie2", "C"), ("_m_h5_tk", "T")]]
    return path, api


def test_five_windows_create_five_accounts_and_repeat_is_idempotent(tmp_path):
    from goofish_bridge.account_sync import sync_accounts

    path, api = setup_sync(tmp_path)
    config = sync_accounts(Config.load(path), api)
    assert len(config.raw["accounts"]) == 5
    original = AccountPaths(tmp_path, "A1").file("cookies.json").read_bytes()
    config = sync_accounts(Config.load(path), api)
    assert len(config.raw["accounts"]) == 5
    assert len(Config.load(path).raw["accounts"]) == 5
    assert AccountPaths(tmp_path, "A1").file("cookies.json").read_bytes() == original


def test_duplicate_uid_and_bad_window_are_skipped(tmp_path):
    from goofish_bridge.account_sync import sync_accounts

    path, api = setup_sync(tmp_path)
    api.cookies.side_effect = [
        [{"name": n, "value": v} for n, v in [("unb", "same"), ("cookie2", "C"), ("_m_h5_tk", "T")]],
    ] * 4 + [BitBrowserError("未登录")]
    config = sync_accounts(Config.load(path), api)
    assert len(config.raw["accounts"]) == 1


def test_bound_window_cannot_change_uid(tmp_path):
    from goofish_bridge.account_sync import sync_accounts

    path, api = setup_sync(tmp_path)
    sync_accounts(Config.load(path), api)
    api.cookies.side_effect = lambda pid: [
        {"name": n, "value": v} for n, v in [("unb", "changed"), ("cookie2", "C"), ("_m_h5_tk", "T")]]
    with pytest.raises(ValueError, match="身份"):
        sync_accounts(Config.load(path), api)


def test_existing_three_accounts_keep_keys_when_two_are_added(tmp_path):
    from goofish_bridge.account import save_login
    from goofish_bridge.account_sync import sync_accounts

    path, api = setup_sync(tmp_path)
    path = write_config(tmp_path, lambda raw: raw.update(bitbrowser={"enabled": True}))
    config = Config.load(path)
    config.initialize_directories()
    for index, account in enumerate(config.raw["accounts"]):
        save_login(config, AccountPaths(tmp_path, account["key"]), api.cookies(f"p{index}"), f"p{index}")
    result = sync_accounts(config, api)
    assert [(a["key"], a["expected_uid"]) for a in result.raw["accounts"]] == [
        (f"A{i + 1}", f"p{i}") for i in range(5)]
    assert len(Config.load(path).raw["accounts"]) == 5


def test_same_name_different_uids_remain_separate(tmp_path):
    from goofish_bridge.account_sync import sync_accounts

    path, api = setup_sync(tmp_path)
    api.profiles.return_value = [BitBrowserProfile(f"p{i}", "同名", "闲鱼") for i in range(5)]
    result = sync_accounts(Config.load(path), api)
    assert len(result.raw["accounts"]) == 5


def test_startup_sync_runs_before_default_account_selection(tmp_path, monkeypatch):
    from goofish_bridge import supervisor
    from goofish_bridge.account_sync import sync_accounts

    path, api = setup_sync(tmp_path)
    config = Config.load(path)
    monkeypatch.setattr("goofish_bridge.account_sync.sync_accounts", lambda c: sync_accounts(c, api))
    seen = []
    monkeypatch.setattr(supervisor, "_run_locked", lambda c, p, keys, *args: seen.extend(keys))
    supervisor.run(config, path, None)
    assert seen == ["A1", "A2", "A3", "A4", "A5"]


def test_empty_group_creates_nothing(tmp_path):
    from goofish_bridge.account_sync import sync_accounts

    path, api = setup_sync(tmp_path)
    api.profiles.return_value = []
    assert sync_accounts(Config.load(path), api).raw["accounts"] == []
