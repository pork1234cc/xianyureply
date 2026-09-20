"""根 Skill 发布边界与已有项目接管回归，不访问真实平台。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bridge_control as control  # noqa: E402
import bundle  # noqa: E402


def make_project(root):
    (root / "src/goofish_bridge").mkdir(parents=True)
    (root / "src/goofish_bridge/__main__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    (root / "config.yaml").write_text("原配置", encoding="utf-8")
    (root / ".venv/Scripts").mkdir(parents=True)
    (root / ".venv/pyvenv.cfg").write_text("", encoding="utf-8")
    (root / ".venv/Scripts/python.exe").write_bytes(b"test")
    return root


def test_root_project_is_selected_before_unrelated_registry(tmp_path, monkeypatch):
    root = make_project(tmp_path / "项目 中文")
    monkeypatch.setattr(control, "SKILL", root)
    monkeypatch.setattr(control, "candidates", lambda: [{"root": "unrelated"}])
    assert control.resolve_instance(None) == root
    assert control.resolve_instance(str(root)) == root


def test_explicit_instance_overrides_project_default(tmp_path, monkeypatch):
    root = make_project(tmp_path / "project")
    target = tmp_path / "other"
    control.atomic_json(target / "instance.json", {"root": str(target), "instance_id": "test"})
    monkeypatch.setattr(control, "SKILL", root)
    assert control.resolve_instance(str(target)) == target


def test_runtime_entry_resolves_its_instance(tmp_path, monkeypatch):
    control.atomic_json(tmp_path / "instance.json", {"root": str(tmp_path), "instance_id": "test"})
    monkeypatch.setattr(control, "SKILL", tmp_path / "runtime")
    assert control.resolve_instance(str(tmp_path)) == tmp_path


def test_existing_root_init_preserves_environment_configuration_and_identity(tmp_path, monkeypatch):
    root = make_project(tmp_path / "project")
    monkeypatch.setattr(control, "SKILL", root)
    monkeypatch.setattr(control, "verify", lambda _: {"version": "test", "files": {}})
    monkeypatch.setattr(control, "interpreter", lambda _: root / ".venv/Scripts/python.exe")
    monkeypatch.setattr(control, "install_environment", lambda _: pytest.fail("不能改动已有环境"))
    assert control.initialize(root)["code"] == "ALREADY_INITIALIZED"
    assert (root / "config.yaml").read_text(encoding="utf-8") == "原配置"
    assert not (root / "instance.json").exists()
    assert not (root / "runtime").exists()
    assert not (root / "management").exists()


def test_root_upgrade_does_not_replace_its_own_code(tmp_path, monkeypatch):
    root = make_project(tmp_path / "project")
    monkeypatch.setattr(control, "SKILL", root)
    monkeypatch.setattr(control, "stop", lambda *_: pytest.fail("不能停服"))
    with pytest.raises(control.ControlError, match="CONFIG_REQUIRED"):
        control.upgrade(root, 1)


def test_release_selection_ignores_private_runtime_files(tmp_path):
    for relative in ("src/example.py", "scripts/example.py", "docs/example.md", "tests/test_example.py",
                     ".env", "config.yaml", "accounts/A1/cookies.json", "data/bridge.sqlite",
                     ".venv/Lib/site-packages/example.py", "backups/secret.py", "instance.json"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("example", encoding="utf-8")
    assert set(bundle.release_files(tmp_path)) == {
        "src/example.py", "scripts/example.py", "docs/example.md", "tests/test_example.py"}


def test_export_is_self_contained_and_never_includes_private_files(tmp_path):
    target = tmp_path / "发布 中文"
    bundle.export(SCRIPTS.parent, target)
    manifest = bundle.verify(target, strict=True)
    assert (target / "SKILL.md").is_file()
    assert (target / "src/goofish_bridge/__main__.py").is_file()
    assert not (target / "skills").exists()
    assert not (target / ".env").exists()
    assert not (target / "data").exists()
    assert set(bundle.files(target)) == set(manifest["files"])
    with pytest.raises(ValueError):
        bundle.export(SCRIPTS.parent, target)


def test_export_rejects_inside_source_and_parent_targets(tmp_path):
    with pytest.raises(ValueError):
        bundle.export(SCRIPTS.parent, SCRIPTS.parent / "new-export")
    with pytest.raises(ValueError):
        bundle.export(SCRIPTS.parent, SCRIPTS.parent.parent)


def test_manifest_cannot_whitelist_secrets(tmp_path):
    target = tmp_path / "release"
    bundle.export(SCRIPTS.parent, target)
    private = target / ".env"
    private.write_text("SECRET=private", encoding="utf-8")
    manifest_path = target / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][".env"] = bundle.digest(private)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        bundle.verify(target)


def test_strict_release_rejects_extra_private_file(tmp_path):
    target = tmp_path / "release"
    bundle.export(SCRIPTS.parent, target)
    (target / "cookies.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        bundle.verify(target, strict=True)


def test_new_instance_uses_one_runtime_copy(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    root.mkdir()
    monkeypatch.setattr(control, "install_environment", lambda _: None)
    monkeypatch.setattr(control, "registry_root", lambda: tmp_path / "registry")
    assert control.initialize(root)["code"] == "INITIALIZED"
    assert (root / "runtime/SKILL.md").is_file()
    assert (root / "runtime/scripts/bridge_control.py").is_file()
    assert (root / "runtime/src/goofish_bridge/__main__.py").is_file()
    assert not (root / "management").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows 升级锁")
def test_upgrade_preserves_old_startup_entry_and_private_data(tmp_path, monkeypatch):
    root = tmp_path / "旧实例 中文"
    for folder in ("runtime", ".venv", "management"):
        (root / folder).mkdir(parents=True)
        (root / folder / "original.txt").write_text("旧资源", encoding="utf-8")
    (root / "config.yaml").write_text("原配置", encoding="utf-8")
    (root / ".env").write_text("PRIVATE=local", encoding="utf-8")
    control.atomic_json(root / "instance.json", {"root": str(root), "instance_id": "keep-id"})
    monkeypatch.setattr(control, "probe", lambda _: {"database_schema": None, "checks": {"configuration": True}})
    monkeypatch.setattr(control, "stop", lambda *_: {})
    monkeypatch.setattr(control, "install_environment", lambda path: (path / ".venv").mkdir())
    result = control.upgrade(root, 1)
    assert result["code"] == "UPGRADED"
    assert (root / "config.yaml").read_text(encoding="utf-8") == "原配置"
    assert (root / ".env").read_text(encoding="utf-8") == "PRIVATE=local"
    assert control.read_json(root / "instance.json")["instance_id"] == "keep-id"
    backup = Path(result["backup"]).parent
    assert (backup / "previous-management/original.txt").is_file()
    assert (root / "runtime/SKILL.md").is_file()
    assert not (root / "management/SKILL.md").exists()
    completed = subprocess.run(
        [sys.executable, str(root / "management/scripts/bridge_control.py"), "--help"],
        cwd=tmp_path, capture_output=True, encoding="utf-8", timeout=20)
    assert completed.returncode == 0, completed.stderr
    assert "inspect" in completed.stdout


def test_startup_targets_the_only_runtime_program(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    (root / ".venv/Scripts").mkdir(parents=True)
    python = root / ".venv/Scripts/python.exe"
    python.write_bytes(b"test")
    python.with_name("pythonw.exe").write_bytes(b"test")
    script = root / "runtime/scripts/bridge_control.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    control.atomic_json(root / "instance.json", {"root": str(root), "instance_id": "test"})
    monkeypatch.setenv("APPDATA", str(tmp_path / "isolated-startup"))
    monkeypatch.setattr(control, "interpreter", lambda _: python)
    monkeypatch.setattr(control, "execute", lambda *_args, **_kwargs: "")
    assert control.install_startup(root)["code"] == "STARTUP_INSTALLED"
    payload = control.read_json(root / "startup-registration.json")
    assert str(script) in payload["arguments"]
    assert not (root / "management").exists()
