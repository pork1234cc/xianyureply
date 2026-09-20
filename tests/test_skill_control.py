"""管理入口离线行为；不使用真实账号或客户。"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("bridge_control", SCRIPTS / "bridge_control.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


def test_internal_management_can_find_own_instance(tmp_path, monkeypatch):
    control.atomic_json(tmp_path / "instance.json", {"root": str(tmp_path), "instance_id": "test"})
    monkeypatch.setattr(control, "SKILL", tmp_path / "management")
    assert control.resolve_instance(str(tmp_path)) == tmp_path


def test_failed_doctor_never_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "evidence", lambda _: {"alive": False, "children_alive": []})
    monkeypatch.setattr(control, "doctor", lambda _: {"ok": False, "code": "CONFIG_REQUIRED"})
    monkeypatch.setattr(control.subprocess, "Popen", lambda *a, **k: pytest.fail("不能启动"))
    assert control.start(tmp_path, 1)["code"] == "CONFIG_REQUIRED"


def test_stale_pid_with_lock_never_writes_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "evidence", lambda _: {"alive": False, "children_alive": []})
    monkeypatch.setattr(control, "lock_available", lambda _: False)
    with pytest.raises(control.ControlError, match="LOCKED"):
        control.stop(tmp_path, 0.01)
    assert not (tmp_path / "data/stop.request").exists()


def test_restart_does_not_start_on_stop_timeout(tmp_path, monkeypatch, capsys):
    if os.name != "nt":
        pytest.skip("Windows 管理锁")
    control.atomic_json(tmp_path / "instance.json", {"root": str(tmp_path), "instance_id": "test"})
    def timeout(*_):
        raise control.ControlError("TIMEOUT", "等待超时")
    monkeypatch.setattr(control, "stop", timeout)
    monkeypatch.setattr(control, "start", lambda *_: pytest.fail("停止未完成不能启动"))
    assert control.main(["restart", "--instance", str(tmp_path)]) == 5
    assert json.loads(capsys.readouterr().out)["code"] == "TIMEOUT"


def test_sqlite_backup_includes_wal(tmp_path):
    root, destination = tmp_path / "实例 空格", tmp_path / "backup"
    root.mkdir()
    with sqlite3.connect(root / "custom.sqlite") as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("CREATE TABLE pending (value TEXT)")
        source.execute("INSERT INTO pending VALUES ('中文队列')")
        source.commit()
        control.backup_instance(root, destination, "custom.sqlite")
    with sqlite3.connect(destination / "database.sqlite") as restored:
        assert restored.execute("SELECT value FROM pending").fetchone()[0] == "中文队列"


def test_bundle_path_escape(tmp_path):
    from bundle import safe_path
    for path in ("../secret", "C:/secret", "a\\secret", "/secret"):
        with pytest.raises(ValueError):
            safe_path(tmp_path, path)


def test_environment_drops_foreign_python_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "another-project")
    env = control.environment(tmp_path)
    assert "PYTHONPATH" not in env
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")


@pytest.mark.skipif(os.name != "nt", reason="Windows 后台生命周期")
def test_detached_start_double_start_and_safe_stop(tmp_path, monkeypatch):
    control.atomic_json(tmp_path / "instance.json", {"root": str(tmp_path), "instance_id": "mock"})
    worker = tmp_path / "mock_runtime.py"
    worker.write_text('''import sys, time
from pathlib import Path
from goofish_bridge.lifecycle import RuntimeEvidence, file_lock
root = Path(sys.argv[1])
with file_lock(root / "data/bridge.lock"), RuntimeEvidence(root) as state:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not (root / "data/stop.request").exists():
        state.update(accounts=[{"key": "A1", "state": "ONLINE"}], feishu_online=True)
        time.sleep(0.05)
''', encoding="utf-8")
    real_popen = control.subprocess.Popen
    def mock_process(command, **kwargs):
        return real_popen([sys.executable, str(worker), str(tmp_path)], **kwargs)
    monkeypatch.setattr(control.subprocess, "Popen", mock_process)
    monkeypatch.setattr(control, "interpreter", lambda _: Path(sys.executable))
    monkeypatch.setattr(control, "doctor", lambda _: {"ok": True})
    try:
        assert control.start(tmp_path, 5)["code"] == "RUNNING"
        assert control.start(tmp_path, 1)["code"] == "ALREADY_RUNNING"
        time.sleep(0.1)
        assert control.evidence(tmp_path)["alive"]
    finally:
        stopped = control.stop(tmp_path, 5)
    assert stopped["code"] == "STOPPED"
    assert not control.evidence(tmp_path)["alive"]


def test_manifest_detects_missing_js_and_tampering(tmp_path):
    from bundle import export, verify
    skill = SCRIPTS.parent
    installed = tmp_path / "skill"
    export(skill, installed)
    verify(installed)
    static = installed / "src/goofish_cli/static/goofish_js_version_2.js"
    static.write_text("被修改", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        verify(installed)


def test_upgrade_dependency_failure_preserves_running_instance(tmp_path, monkeypatch):
    from bundle import files
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/original.txt").write_text("旧版本", encoding="utf-8")
    previous = files(tmp_path / "runtime")
    monkeypatch.setattr(control, "probe", lambda _: {"database_schema": 4})
    monkeypatch.setattr(control, "stop", lambda *_: pytest.fail("准备失败不能停止"))
    def failure(_):
        raise control.ControlError("ENVIRONMENT_REQUIRED", "模拟依赖失败")
    monkeypatch.setattr(control, "install_environment", failure)
    with pytest.raises(control.ControlError, match="ENVIRONMENT_REQUIRED"):
        control.upgrade(tmp_path, 1)
    assert files(tmp_path / "runtime") == previous


def test_status_partial_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "evidence", lambda _: {
        "alive": True, "heartbeat_fresh": True, "phase": "RUNNING", "children_alive": [],
        "accounts": [{"key": "A1", "state": "AUTH_REQUIRED"}], "feishu_online": True})
    monkeypatch.setattr(control, "probe", lambda _: {"queue": None, "checks": {}})
    assert control.status(tmp_path)["code"] == "PARTIAL_READY"


def test_alternative_environment_not_silently_replaced(tmp_path, monkeypatch):
    other = tmp_path / "venv_clean"
    (other / "Scripts").mkdir(parents=True)
    (other / "pyvenv.cfg").write_text("home = example", encoding="utf-8")
    (other / "Scripts/python.exe").write_bytes(b"placeholder")
    monkeypatch.setattr(control, "execute", lambda *_args, **_kwargs: pytest.fail("不能另建环境"))
    with pytest.raises(control.ControlError, match="ENVIRONMENT_REQUIRED"):
        control.install_environment(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows 升级互斥锁")
def test_upgrade_switch_failure_restores_code_without_restoring_database(tmp_path, monkeypatch):
    for name in ("runtime", ".venv"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "original.txt").write_text("旧版本", encoding="utf-8")
    (tmp_path / "queue.sqlite").write_bytes("不要回滚队列".encode())
    monkeypatch.setattr(control, "probe", lambda _: {"database_schema": None})
    monkeypatch.setattr(control, "stop", lambda *_: {})
    def install(root):
        if root == tmp_path:
            (root / ".venv").mkdir()
            raise control.ControlError("ENVIRONMENT_REQUIRED", "模拟切换失败")
    monkeypatch.setattr(control, "install_environment", install)
    with pytest.raises(control.ControlError, match="UPGRADE_FAILED"):
        control.upgrade(tmp_path, 1)
    for name in ("runtime", ".venv"):
        assert (tmp_path / name / "original.txt").read_text(encoding="utf-8") == "旧版本"
    assert (tmp_path / "queue.sqlite").read_bytes() == "不要回滚队列".encode()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell UTF-8 入口")
def test_bootstrap_executes_in_windows_powershell(tmp_path):
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / "bootstrap.ps1"),
         "-Python", sys.executable, "-Operation", "inspect"],
        cwd=tmp_path, capture_output=True, encoding="utf-8", errors="replace", timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["operation"] == "inspect"
