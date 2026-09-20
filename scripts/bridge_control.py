"""Windows 消息桥管理入口；stdout 一个 JSON，所有等待均有界。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from bundle import digest, export, verify  # noqa: E402

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "src"))
from goofish_bridge.lifecycle import (  # noqa: E402
    atomic_json,
    evidence,
    file_lock,
    lock_available,
    read_json,
)

EXIT_CODES = {"CONFIG_REQUIRED": 2, "LOGIN_REQUIRED": 2, "ENVIRONMENT_REQUIRED": 2,
              "UNSUPPORTED_PLATFORM": 3, "LOCKED": 4, "TIMEOUT": 5}


class ControlError(Exception):
    def __init__(self, code, action):
        self.code, self.action = code, action
        super().__init__(code)


def result(operation, root=None, code="OK", ok=True, **extra):
    return {"schema_version": 1, "operation": operation, "ok": ok, "code": code,
            "instance_id": read_json(root / "instance.json").get("instance_id") if root else None,
            "warnings": [], "next_action": None, **extra}


def environment(root):
    env = os.environ.copy()
    venv = root / ".venv"
    env.update(VIRTUAL_ENV=str(venv), UV_PROJECT_ENVIRONMENT=str(venv),
               PATH=str(venv / "Scripts") + os.pathsep + env.get("PATH", ""),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    # 不让维护仓库的 Python 搜索路径影响安装产物。
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def execute(command, root, timeout=60, env=None):
    try:
        completed = subprocess.run(command, cwd=root, env=env or environment(root),
                                   capture_output=True, encoding="utf-8", errors="replace",
                                   timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise ControlError("TIMEOUT", "命令等待超时；检查实例后重试，不强杀消息桥") from exc
    if completed.returncode:
        # 外部错误可能带认证地址，不转印 stderr。
        raise ControlError("ENVIRONMENT_REQUIRED", "依赖或配置命令失败，请在本机检查环境")
    return completed.stdout


def interpreter(root):
    python = root / ".venv/Scripts/python.exe"
    if not python.is_file() or not (root / ".venv/pyvenv.cfg").is_file():
        raise ControlError("ENVIRONMENT_REQUIRED", "实例 .venv 缺失或损坏，请执行 init")
    output = execute([str(python), "-c", "import sys,json; print(json.dumps({'prefix':sys.prefix,'version':list(sys.version_info[:2])}))"], root)
    info = json.loads(output)
    if Path(info["prefix"]).resolve() != (root / ".venv").resolve() or info["version"] < [3, 11]:
        raise ControlError("ENVIRONMENT_REQUIRED", "实例解释器路径或 Python 版本不符合要求")
    return python


def probe(root):
    python = interpreter(root)
    return json.loads(execute([str(python), "-m", "goofish_bridge.management_probe", str(root)], root))


def registry_root():
    local = os.getenv("LOCALAPPDATA")
    if not local:
        raise ControlError("CONFIG_REQUIRED", "请显式指定 --instance 绝对路径")
    return Path(local) / "XianyuFeishuBridge/registry"


def candidates():
    items = []
    for record in registry_root().glob("*.json"):
        item = read_json(record)
        root = Path(item.get("root", ""))
        if root.is_absolute() and read_json(root / "instance.json").get("instance_id") == item.get("instance_id"):
            items.append(item)
    return items


def is_project(root):
    return (root / "pyproject.toml").is_file() and (root / "src/goofish_bridge/__main__.py").is_file()


def existing_project(root):
    return is_project(root) and (root / "config.yaml").is_file() and (root / ".venv/pyvenv.cfg").is_file()


def resolve_instance(value, initializing=False):
    if value:
        root = Path(value)
        if not root.is_absolute():
            raise ControlError("CONFIG_REQUIRED", "--instance 必须使用绝对路径")
        root = root.resolve()
    elif existing_project(SKILL):
        root = SKILL
    elif SKILL.name in {"runtime", "management"} and read_json(SKILL.parent / "instance.json"):
        root = SKILL.parent
    elif existing_project(Path.cwd()):
        root = Path.cwd().resolve()
    else:
        items = candidates()
        if not items and initializing:
            return SKILL
        if len(items) != 1:
            raise ControlError("CONFIG_REQUIRED", "无唯一已登记实例，请指定 --instance")
        root = Path(items[0]["root"]).resolve()
    local_project = is_project(root)
    internal_control = not initializing and SKILL in {root / "management", root / "runtime"}
    if not local_project and not internal_control and (root.is_relative_to(SKILL) or SKILL.is_relative_to(root)):
        raise ControlError("CONFIG_REQUIRED", "独立数据目录不能嵌套在 Skill 内；已有根项目可直接管理")
    if not initializing:
        metadata = read_json(root / "instance.json")
        if not (root / "instance.json").exists() and existing_project(root):
            return root
        if metadata.get("root") != str(root) or not metadata.get("instance_id"):
            raise ControlError("CONFIG_REQUIRED", "目标不是有效实例或已有项目，请检查配置和环境")
    return root


def install_environment(root):
    uv = shutil.which("uv")
    if not uv or not shutil.which("node"):
        raise ControlError("ENVIRONMENT_REQUIRED", "需要 uv 和 Node.js；不安装到全局 Python")
    venv = root / ".venv"
    if venv.exists():
        interpreter(root)
    else:
        alternatives = [p for p in root.iterdir() if p.is_dir() and (p / "pyvenv.cfg").exists()]
        if alternatives:
            raise ControlError("ENVIRONMENT_REQUIRED", "目录已有其他名称的环境；本版实例要求 .venv，请选择空部署目录或先明确环境适配")
        print("创建实例专用 .venv 并安装锁定依赖。", file=sys.stderr)
        execute([uv, "venv", str(venv), "--python", getattr(sys, "_base_executable", sys.executable)], root, timeout=180)
    project = root if is_project(root) else root / "runtime"
    command = [uv, "sync", "--project", str(project), "--locked", "--no-dev"]
    if project != root:
        command.append("--no-editable")
    execute(command, root, timeout=600)
    interpreter(root)


def initialize(root):
    manifest = verify(SKILL)
    if is_project(root):
        if (root / "config.yaml").exists():
            # 原项目直接复用，不重装环境，也不改变运行进程使用的实例身份。
            interpreter(root)
            return result("init", root, "ALREADY_INITIALIZED",
                          next_action="已沿用现有项目，使用 doctor/status 检查")
        if any((root / name).exists() for name in ("accounts", "data", ".env")):
            raise ControlError("CONFIG_REQUIRED", "已有数据但缺少 config.yaml，请恢复原配置，不自动生成替代配置")
        install_environment(root)
        for source, target in (("config.example.yaml", "config.yaml"), (".env.example", ".env")):
            if not (root / target).exists():
                shutil.copyfile(root / source, root / target)
        return result("init", root, "INITIALIZED", next_action="在本目录填写配置并完成绑定，然后 doctor/start")
    metadata_path = root / "instance.json"
    metadata = read_json(metadata_path)
    if metadata_path.exists() and metadata.get("root") != str(root):
        raise ControlError("CONFIG_REQUIRED", "实例元数据不匹配")
    if not metadata and any((root / p).exists() for p in ("config.yaml", "accounts", "data", "runtime")):
        raise ControlError("CONFIG_REQUIRED", "目录含已有项目数据；请指定空目录，现有项目不自动迁移")
    if not metadata:
        metadata = {"schema_version": 1, "instance_id": uuid.uuid4().hex, "root": str(root),
                    "version": manifest["version"], "config": str(root / "config.yaml"),
                    "python": str(root / ".venv/Scripts/python.exe"), "initialized": False}
        atomic_json(metadata_path, metadata)
    if metadata.get("initialized"):
        interpreter(root)
        return result("init", root, "ALREADY_INITIALIZED")
    runtime = root / "runtime"
    if not runtime.exists():
        export(SKILL, runtime)
    for relative, expected in manifest["files"].items():
        actual = runtime / relative
        if not actual.is_file() or digest(actual) != expected:
            raise ControlError("CONFIG_REQUIRED", "未完成初始化的运行副本有改动或缺失，请保留检查后处理")
    for source, target in (("config.example.yaml", "config.yaml"), (".env.example", ".env")):
        if not (root / target).exists():
            shutil.copyfile(runtime / source, root / target)
    install_environment(root)
    # 自启直接使用 runtime 内入口，不再复制第二份完整管理程序。
    metadata["initialized"] = True
    metadata["bundle_files"] = manifest["files"]
    atomic_json(metadata_path, metadata)
    atomic_json(registry_root() / f"{metadata['instance_id']}.json",
                {k: metadata[k] for k in ("instance_id", "root", "python", "config")})
    return result("init", root, "INITIALIZED", next_action="在实例 config.yaml 和 .env 填写配置，完成人工登录及飞书绑定")


def status(root, operation="status"):
    process = evidence(root)
    locked = not lock_available(root / "data/bridge.lock")
    data = probe(root)
    if process["alive"]:
        code = "RUNNING" if process["heartbeat_fresh"] and process["phase"] == "RUNNING" else "STARTING_OR_UNRESPONSIVE"
        if code == "RUNNING" and (not process["accounts"] or not process["feishu_online"]
                                  or any(a["state"] != "ONLINE" for a in process["accounts"])):
            code = "PARTIAL_READY"
    else:
        code = "LOCKED" if locked or process["children_alive"] else "STOPPED"
    return result(operation, root, code, process=process,
                  accounts=process["accounts"], queue=data.get("queue"),
                  database_accounts=data.get("database_accounts", []),
                  checks=data.get("checks"), bridge_locked=locked)


def doctor(root):
    state = status(root, "doctor")
    missing = [name for name, passed in state["checks"].items() if not passed]
    if missing:
        code = "LOGIN_REQUIRED" if all(k in {"accounts", "account_bindings", "feishu_binding"} for k in missing) else "CONFIG_REQUIRED"
        state.update(ok=False, code=code, next_action="补齐检查项：" + "、".join(missing))
    return state


def start(root, timeout):
    current = evidence(root)
    if current["alive"]:
        return result("start", root, "ALREADY_RUNNING", process=current)
    if current["children_alive"] or not lock_available(root / "data/bridge.lock"):
        raise ControlError("LOCKED", "存在旧实例或子进程，先核对运行状态")
    check = doctor(root)
    if not check["ok"]:
        check["operation"] = "start"
        return check
    python = interpreter(root)
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    run_id = uuid.uuid4().hex
    with (logs / f"start-{run_id}.stdout.log").open("w", encoding="utf-8") as out, (logs / f"start-{run_id}.stderr.log").open("w", encoding="utf-8") as err:
        child = subprocess.Popen([str(python), "-m", "goofish_bridge.management_probe", str(root), "--run"],
                                 cwd=root, env=environment(root), stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                 creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = evidence(root)
        if child.poll() is not None:
            raise ControlError("START_FAILED", "进程已退出，请查看本机日志的脱敏错误")
        if current["alive"] and current["heartbeat_fresh"] and current["phase"] == "RUNNING":
            states = [a["state"] for a in current["accounts"]]
            if states and all(s == "ONLINE" for s in states) and current["feishu_online"]:
                return result("start", root, "RUNNING", process=current)
            if any(s in {"AUTH_REQUIRED", "LOGIN_REQUIRED"} for s in states):
                return result("start", root, "LOGIN_REQUIRED", ok=False, process=current,
                              next_action="检查对应账号实时凭据；浏览器登录有效不代表 Cookie 快照有效")
        time.sleep(0.25)
    current = evidence(root)
    if current["alive"] and current["heartbeat_fresh"] and current["phase"] == "RUNNING":
        return result("start", root, "PARTIAL_READY", process=current,
                      warnings=["主循环存活，但飞书或部分账号尚未就绪"])
    raise ControlError("TIMEOUT", "启动未在截止时间内就绪；进程可能仍在初始化，请先查询状态")


def stop(root, timeout):
    current = evidence(root)
    if not current["alive"]:
        if current["children_alive"] or not lock_available(root / "data/bridge.lock"):
            raise ControlError("LOCKED", "无法确认旧实例归属，不写停止请求或强杀进程")
        return result("stop", root, "ALREADY_STOPPED", process=current)
    if current["phase"] == "STARTING":
        raise ControlError("LOCKED", "实例仍在启动同步，待进入主循环再停止")
    # 停止标记是既有协议，不直接给 PID 发信号。
    (root / "data/stop.request").write_text(str(time.time()), encoding="utf-8")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = evidence(root)
        if not state["alive"] and not state["children_alive"] and lock_available(root / "data/bridge.lock"):
            return result("stop", root, "STOPPED", process=state)
        time.sleep(0.25)
    raise ControlError("TIMEOUT", "安全停止超时；保留进程与队列，请检查状态，不自动强杀")


def backup_instance(root, destination, database):
    destination.mkdir(parents=True)
    for name in ("config.yaml", ".env", "instance.json"):
        if (root / name).is_file():
            shutil.copyfile(root / name, destination / name)
    for name in ("accounts", "data"):
        folder = root / name
        if folder.exists():
            shutil.copytree(folder, destination / name,
                            ignore=shutil.ignore_patterns("*.sqlite*", "*.db*", "*.lock", "stop.request", "runtime-state.json"))
    if database and (root / database).is_file():
        target = destination / "database.sqlite"
        with sqlite3.connect(f"{(root / database).as_uri()}?mode=ro", uri=True) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
    from bundle import files
    atomic_json(destination / "backup-manifest.json", {"files": files(destination), "database_source": database})


def upgrade(root, timeout):
    if is_project(root):
        raise ControlError("CONFIG_REQUIRED", "当前目录就是完整 Skill；修改或更新根源码后验证，依赖变更在停服维护时处理，不创建第二套实例")
    manifest = verify(SKILL)
    metadata = read_json(root / "instance.json")
    if metadata.get("bundle_files") == manifest["files"]:
        return result("upgrade", root, "ALREADY_CURRENT")
    before = probe(root)
    if before.get("database_schema") not in (None, 4):
        raise ControlError("CONFIG_REQUIRED", "首版仅支持数据库 schema 4；其他版本需明确迁移方案")
    preparation = root / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    stage = preparation / "prepared"
    stage.mkdir(parents=True)
    export(SKILL, stage / "runtime")
    install_environment(stage)
    was_running = evidence(root)["alive"]
    stop(root, timeout)
    # 与原 run 共用锁，封住直接 CLI 在切换期间重新启动的窗口。
    with file_lock(root / "data/bridge.lock"):
        backup_instance(root, preparation / "snapshot", before.get("database"))
        moved = []
        try:
            for name, previous in (("runtime", "previous-runtime"), (".venv", "previous-venv")):
                (root / name).rename(preparation / previous)
                moved.append((name, previous))
            shutil.copytree(stage / "runtime", root / "runtime")
            install_environment(root)
            validated = probe(root)
            if validated.get("checks", {}).get("configuration") is False:
                raise ControlError("CONFIG_REQUIRED", "新版无法读取实例配置")
            if (root / "management").exists():
                (root / "management").rename(preparation / "previous-management")
                moved.append(("management", "previous-management"))
                legacy = root / "management/scripts/bridge_control.py"
                legacy.parent.mkdir(parents=True)
                legacy.write_text(
                    '"""兼容旧快捷方式，转到实例内唯一程序入口。"""\n'
                    'import runpy\nimport sys\nfrom pathlib import Path\n'
                    'try:\n    sys.stdout.reconfigure(encoding="utf-8")\n'
                    '    sys.stderr.reconfigure(encoding="utf-8")\n'
                    'except (AttributeError, OSError):\n    pass\n'
                    'entry = Path(__file__).resolve().parents[2] / "runtime/scripts/bridge_control.py"\n'
                    'sys.path.insert(0, str(entry.parent))\n'
                    'runpy.run_path(str(entry), run_name="__main__")\n', encoding="utf-8")
            metadata.update(version=manifest["version"], bundle_files=manifest["files"],
                            last_backup=str(preparation / "snapshot"))
            atomic_json(root / "instance.json", metadata)
        except (ControlError, OSError, ValueError):
            # 此时尚未启动新版，不恢复数据库；保留失败文件并回到原代码环境。
            for name, previous in reversed(moved):
                if (root / name).exists():
                    (root / name).rename(preparation / ("failed-" + name.lstrip(".")))
                (preparation / previous).rename(root / name)
            raise ControlError("UPGRADE_FAILED", "旧代码和环境已恢复，实例保持停止；备份及失败资源已保留") from None
    outcome = start(root, timeout) if was_running else result("upgrade", root, "UPGRADED")
    outcome.update(operation="upgrade", backup=str(preparation / "snapshot"))
    return outcome


def install_startup(root):
    metadata = read_json(root / "instance.json")
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    identity = metadata.get("instance_id") or hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    target = startup / f"XianyuFeishuBridge-{identity}.lnk"
    if target.exists():
        raise ControlError("LOCKED", "同实例启动项已存在，不覆盖；请核对已有启动项")
    python = interpreter(root)
    script = root / "scripts/bridge_control.py" if is_project(root) else root / "runtime/scripts/bridge_control.py"
    if not script.is_file():
        script = root / "management/scripts/bridge_control.py"
    python_windowless = python.with_name("pythonw.exe")
    if not script.is_file() or not python_windowless.is_file():
        raise ControlError("CONFIG_REQUIRED", "实例缺少独立管理资源")
    # 参数由 JSON 文件传递给固定 PowerShell，避免路径插入脚本代码。
    payload = root / "startup-registration.json"
    arguments = subprocess.list2cmdline([str(script), "start", "--instance", str(root)])
    atomic_json(payload, {"target": str(target), "python": str(python_windowless), "arguments": arguments, "root": str(root)})
    command = "$p=Get-Content -LiteralPath $env:BRIDGE_STARTUP_PAYLOAD -Raw -Encoding UTF8 | ConvertFrom-Json; $s=(New-Object -ComObject WScript.Shell).CreateShortcut($p.target); $s.TargetPath=$p.python; $s.Arguments=$p.arguments; $s.WorkingDirectory=$p.root; $s.WindowStyle=7; $s.Save()"
    env = environment(root)
    env["BRIDGE_STARTUP_PAYLOAD"] = str(payload)
    execute(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], root, env=env)
    return result("install-startup", root, "STARTUP_INSTALLED", startup=str(target))


def main(argv=None):
    parser = argparse.ArgumentParser(description="闲鱼飞书消息桥实例管理")
    parser.add_argument("operation", choices=["inspect", "init", "doctor", "start", "stop", "restart", "status", "diagnose", "upgrade", "install-startup"])
    parser.add_argument("--instance")
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args(argv)
    root = None
    try:
        if not 0 < args.timeout <= 600:
            raise ControlError("CONFIG_REQUIRED", "等待时间必须大于 0 且不超过 600 秒")
        if os.name != "nt":
            raise ControlError("UNSUPPORTED_PLATFORM", "首版需要可执行 Windows 本机命令的宿主")
        if args.operation == "inspect":
            output = result("inspect", tools={name: shutil.which(name) for name in ("uv", "node", "powershell")},
                            python=sys.executable, python_version=list(sys.version_info[:3]), instances=candidates())
        else:
            root = resolve_instance(args.instance, args.operation == "init")
            if args.operation in {"status", "diagnose"}:
                output = status(root, args.operation)
            elif args.operation == "doctor":
                output = doctor(root)
            else:
                with file_lock(root / ".management.lock"):
                    if args.operation == "init":
                        output = initialize(root)
                    elif args.operation == "start":
                        output = start(root, args.timeout)
                    elif args.operation == "stop":
                        output = stop(root, args.timeout)
                    elif args.operation == "restart":
                        stop(root, args.timeout)
                        output = start(root, args.timeout)
                        output["operation"] = "restart"
                    elif args.operation == "upgrade":
                        output = upgrade(root, args.timeout)
                    else:
                        output = install_startup(root)
    except BlockingIOError:
        output = result(args.operation, root, "LOCKED", False, next_action="另一操作持有实例锁，稍后重试")
    except ControlError as exc:
        output = result(args.operation, root, exc.code, False, next_action=exc.action)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        output = result(args.operation, root, "ERROR", False, next_action="本地资源或元数据异常；检查完整性和实例配置")
    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["ok"] else EXIT_CODES.get(output["code"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
