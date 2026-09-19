"""比特浏览器分组 Cookie 读取模块测试。"""

from __future__ import annotations

import time

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


def test_cookies_for_account_reads_detail_cookie_json(monkeypatch):
    client = BitBrowserClient("http://bitbrowser")

    monkeypatch.setattr(client, "profiles", lambda _: [
        type("Profile", (), {"profile_id": "p1", "name": "A1"})(),
    ])
    monkeypatch.setattr(client, "_post", lambda path, body: {
        "cookie": '[{"name":"unb","value":"U"},{"name":"_m_h5_tk","value":"T"},'
                  '{"name":"cookie2","value":"C"}]',
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
                  '{"name":"cookie2","value":"C"}]',
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
    assert client.cookies("profile-1")[0]["value"] == "U"
    assert [path for path, _ in calls] == [
        "/browser/cookies/get", "/browser/pids",
        "/browser/detail", "/browser/open", "/browser/cookies/get", "/browser/close",
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
