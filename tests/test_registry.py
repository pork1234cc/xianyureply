"""验 registry discover 能找到所有命令且无重名。"""
from __future__ import annotations


def test_discover_all_commands():
    from goofish_cli.core.registry import discover, iter_commands

    discover()
    names = [c.full_name for c in iter_commands()]
    # 仅保留登录与消息诊断命令
    expected = {
        "auth.login",
        "auth.status",
        "auth.reset-guard",
        "message.list-chats",
        "message.history",
        "message.watch",
        "message.send",
    }
    assert set(names) == expected


def test_write_commands_marked():
    from goofish_cli.core.registry import discover, registry

    discover()
    r = registry()
    assert r["message.send"].write is True
    assert r["message.history"].write is False
    assert r["message.list-chats"].write is False
