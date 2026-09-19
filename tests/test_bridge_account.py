"""身份绑定和凭据保存保护，不访问真实账号。"""

import pytest

from goofish_bridge.account import bound_uid, save_login, validate_uid
from goofish_bridge.config import AccountPaths, Config
from tests.test_bridge_config import write_config


def setup_account(tmp_path):
    config = Config.load(write_config(tmp_path))
    config.initialize_directories()
    return config, AccountPaths(tmp_path, "A1")


def records(uid):
    return [{"name": "unb", "value": uid}, {"name": "_m_h5_tk", "value": "fake_token"}]


def test_login_requires_explicit_uid(tmp_path):
    config, paths = setup_account(tmp_path)
    with pytest.raises(ValueError):
        save_login(config, paths, records("user-a"), "user-b")
    assert not paths.file("cookies.json").exists()


def test_wrong_account_never_overwrites_cookie(tmp_path):
    config, paths = setup_account(tmp_path)
    save_login(config, paths, records("user-a"), "user-a")
    original = paths.file("cookies.json").read_bytes()
    with pytest.raises(ValueError):
        save_login(config, paths, records("user-b"), "user-b")
    assert paths.file("cookies.json").read_bytes() == original
    assert bound_uid(paths) == "user-a"


def test_same_uid_cannot_bind_another_slot(tmp_path):
    config, paths = setup_account(tmp_path)
    save_login(config, paths, records("user-a"), "user-a")
    with pytest.raises(ValueError):
        validate_uid(config, AccountPaths(tmp_path, "A2"), "user-a")


def test_renewal_keeps_backup(tmp_path):
    config, paths = setup_account(tmp_path)
    save_login(config, paths, records("user-a"), "user-a")
    original = paths.file("cookies.json").read_bytes()
    save_login(config, paths, records("user-a"), "user-a")
    backups = list(paths.directory.glob("cookies-backup-*.bin"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
