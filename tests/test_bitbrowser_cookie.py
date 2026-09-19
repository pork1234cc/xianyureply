"""比特浏览器分组 Cookie 读取模块测试。"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from goofish_bridge.bitbrowser_cookie import BitBrowserClient, BitBrowserError


def test_profiles_filters_group_and_goofish(monkeypatch):
    client = BitBrowserClient("http://bitbrowser")

    def fake_post(path, body):
        if path == "/group/list":
            return {"list": [{"id": "g1", "groupName": "闲鱼"}]}
        assert path == "/browser/list"
        assert body["groupId"] == "g1"
        return {"list": [
            {"id": "p1", "name": "A1", "platform": "https://www.goofish.com/"},
            {"id": "p2", "name": "其他", "platform": "https://example.com/"},
        ]}

    monkeypatch.setattr(client, "_post", fake_post)
    assert client.profiles("闲鱼")[0].profile_id == "p1"


def test_profiles_reads_all_pages_and_windows_without_platform(monkeypatch):
    client = BitBrowserClient("http://bitbrowser")

    def post(path, body):
        if path == "/group/list":
            return {"list": [{"id": "g", "groupName": "闲鱼"}]}
        if body["page"] == 0:
            return {"list": [{"id": f"p{i}", "name": str(i)} for i in range(100)]}
        return {"list": [{"id": "last", "name": "末页"}]}

    monkeypatch.setattr(client, "_post", post)
    assert len(client.profiles("闲鱼")) == 101


def test_cookies_for_account_reads_detail_cookie_json(monkeypatch):
    client = BitBrowserClient("http://bitbrowser")

    monkeypatch.setattr(client, "profiles", lambda _: [
        type("Profile", (), {"profile_id": "p1", "name": "A1"})(),
    ])
    monkeypatch.setattr(client, "_post", lambda path, body: {
        "cookie": '[{"name":"unb","value":"U"},{"name":"_m_h5_tk","value":"T"},'
                  '{"name":"cookie2","value":"C"}]'.replace('"T"', '"T_9999999999999"'),
    } if path == "/browser/detail" else None)
    records = client.cookies_for_account("闲鱼", "A1", "U")
    assert {item["name"] for item in records} == {"unb", "_m_h5_tk", "cookie2"}


def test_cookie_uid_mismatch_is_rejected(monkeypatch):
    client = BitBrowserClient("http://bitbrowser")
    monkeypatch.setattr(client, "profiles", lambda _: [
        type("Profile", (), {"profile_id": "p1", "name": "A1"})(),
    ])
    monkeypatch.setattr(client, "_post", lambda path, body: {
        "cookie": '[{"name":"unb","value":"OTHER"},{"name":"_m_h5_tk","value":"T"},'
                  '{"name":"cookie2","value":"C"}]'.replace('"T"', '"T_9999999999999"'),
    } if path == "/browser/detail" else None)
    with pytest.raises(BitBrowserError, match="UID"):
        client.cookies_for_account("闲鱼", "A1", "EXPECTED")


def test_headless_open_ignores_default_urls(monkeypatch):
    client = BitBrowserClient("http://bitbrowser")
    calls = []
    monkeypatch.setattr(client, "_post", lambda path, body: calls.append((path, body)))
    client.open_headless("profile-1")
    assert calls == [("/browser/open", {
        "id": "profile-1", "args": ["--headless"], "queue": True,
        "ignoreDefaultUrls": True,
    })]


def test_live_cookies_take_priority_without_opening_or_closing_window(monkeypatch):
    client = BitBrowserClient("http://bitbrowser")
    calls = []

    def fake_post(path, body):
        calls.append(path)
        if path == "/browser/cookies/get":
            assert body == {"browserId": "profile-1"}
            return [{"name": "unb", "value": "LIVE"}]
        return {"cookie": '[{"name":"unb","value":"STALE"}]'}

    monkeypatch.setattr(client, "_post", fake_post)
    assert client.cookies("profile-1")[0]["value"] == "LIVE"
    assert calls == ["/browser/cookies/get"]


def test_realtime_cookie_fetch_closes_temporary_window(monkeypatch):
    client = BitBrowserClient("http://bitbrowser")
    calls = []

    def fake_post(path, body):
        calls.append((path, body))
        if path == "/browser/detail":
            return {"cookie": ""}
        if path == "/browser/cookies/get":
            return [{"name": "unb", "value": "U"}] if any(
                call[0] == "/browser/open" for call in calls
            ) else []
        return None

    monkeypatch.setattr(client, "_post", fake_post)
    monkeypatch.setattr(client, "_load_headless_page", lambda opened: [{"name": "unb", "value": "U"}])
    assert client.cookies("profile-1")[0]["value"] == "U"
    assert [path for path, _ in calls] == [
        "/browser/cookies/get", "/browser/pids",
        "/browser/detail", "/browser/open", "/browser/close", "/browser/pids",
    ]


def test_empty_live_cookies_never_close_existing_window(monkeypatch):
    client = BitBrowserClient("http://bitbrowser")
    calls = []

    def fake_post(path, body):
        calls.append(path)
        return {"profile-1": 123} if path == "/browser/pids" else []

    monkeypatch.setattr(client, "_post", fake_post)
    with pytest.raises(BitBrowserError, match="尚未取得 Cookie"):
        client.cookies("profile-1")
    assert calls == ["/browser/cookies/get", "/browser/pids"]


@pytest.mark.parametrize("path,statuses,expected_calls", [
    ("/group/list", [429, 200], 2),
    ("/browser/detail", [429] * 5, 5),
    ("/browser/open", [429], 1),
    ("/group/list", [403], 1),
])
def test_read_api_rate_limit_is_bounded_and_writes_are_not_retried(monkeypatch, path, statuses, expected_calls):
    calls, sleeps = [], []

    def fake_post(*args, **kwargs):
        calls.append(args)
        response = requests.Response()
        response.status_code = statuses[len(calls) - 1]
        response._content = b'{"success":true,"data":{"list":[]}}'
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    client = BitBrowserClient("http://bitbrowser")
    if statuses[-1] == 200:
        assert client._post(path, {}) == {"list": []}
    else:
        with pytest.raises(BitBrowserError):
            client._post(path, {})
    assert len(calls) == expected_calls
    assert sleeps == [2 ** index for index in range(expected_calls - 1)]


@pytest.mark.parametrize("token", ["T_1", "T", "", "T_1000000"])
def test_expired_or_invalid_snapshot_uses_headless_and_closes(monkeypatch, token):
    client = BitBrowserClient("http://bitbrowser")
    calls = []
    snapshot = [{"name": name, "value": value} for name, value in
                [("unb", "U"), ("cookie2", "C"), ("_m_h5_tk", token)]]

    def fake_post(path, body):
        calls.append(path)
        return {"cookie": snapshot} if path == "/browser/detail" else None

    monkeypatch.setattr(client, "_post", fake_post)
    monkeypatch.setattr(client, "_load_headless_page", lambda opened: [{"name": "unb", "value": "NEW"}], raising=False)
    assert client.cookies("p1")[0]["value"] == "NEW"
    assert calls[-3:] == ["/browser/open", "/browser/close", "/browser/pids"]


@pytest.mark.parametrize("failure", ["open", "load"])
def test_headless_failure_still_closes_window(monkeypatch, failure):
    client = BitBrowserClient("http://bitbrowser")
    calls = []

    def fake_post(path, body):
        calls.append(path)
        if path == "/browser/open" and failure == "open":
            raise BitBrowserError("打开失败")
        return None

    def fail_load(opened):
        raise BitBrowserError("加载失败")

    monkeypatch.setattr(client, "_post", fake_post)
    monkeypatch.setattr(client, "_load_headless_page", fail_load)
    with pytest.raises(BitBrowserError, match="失败"):
        client.cookies("p1")
    assert calls[-2:] == ["/browser/close", "/browser/pids"]


@pytest.mark.parametrize("ready", [True, False])
def test_headless_load_waits_for_fresh_cookie_and_always_closes(monkeypatch, ready):
    import playwright.sync_api

    client = BitBrowserClient("http://bitbrowser")
    calls = []
    fresh = [{"name": name, "value": value} for name, value in
             [("unb", "U"), ("cookie2", "C"), ("_m_h5_tk", "T_9999999999999")]]
    page, context, chromium = MagicMock(), MagicMock(), MagicMock()
    context.new_page.return_value = page
    context.cookies.side_effect = [[], fresh] if ready else [[]] * 30
    chromium.connect_over_cdp.return_value.contexts = [context]
    manager = MagicMock()
    manager.__enter__.return_value = SimpleNamespace(chromium=chromium)
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: manager)

    def fake_post(path, body):
        calls.append(path)
        return {"ws": "ws://localhost/test"} if path == "/browser/open" else None

    monkeypatch.setattr(client, "_post", fake_post)
    if ready:
        assert client.cookies("p1")[0]["value"] == "U"
        page.wait_for_timeout.assert_called_once_with(1000)
    else:
        with pytest.raises(BitBrowserError, match="仍过期"):
            client.cookies("p1")
        assert page.wait_for_timeout.call_count == 30
    page.goto.assert_called_once_with("https://www.goofish.com/", wait_until="domcontentloaded", timeout=30000)
    assert calls[-2:] == ["/browser/close", "/browser/pids"]


def test_close_failure_is_reported(monkeypatch):
    client = BitBrowserClient("http://bitbrowser")
    monkeypatch.setattr(client, "_post", lambda path, body: {"p1": 123})
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(BitBrowserError, match="未确认关闭"):
        client.close("p1")


@pytest.mark.parametrize("seconds,expected", [(0, False), (60, False), (61, True)])
def test_snapshot_expiry_boundary(monkeypatch, seconds, expected):
    monkeypatch.setattr(time, "time", lambda: 1000)
    records = [{"name": name, "value": value} for name, value in
               [("unb", "U"), ("cookie2", "C"), ("_m_h5_tk", f"T_{(1000 + seconds) * 1000}")]]
    assert BitBrowserClient._fresh(records) is expected
