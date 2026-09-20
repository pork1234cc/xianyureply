"""不加载配置或凭据的 Windows 实例锁与进程证据。"""

from __future__ import annotations

import ctypes
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def process_identity(pid: int) -> dict | None:
    """用内核进程创建时间防止 PID 复用；查询失败不猜测存活。"""
    if os.name != "nt" or not isinstance(pid, int) or pid <= 0:
        return None
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return {"pid": pid, "created": (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime}
    finally:
        kernel.CloseHandle(handle)


def same_process(identity: dict) -> bool:
    return bool(identity) and process_identity(identity.get("pid")) == identity


@contextmanager
def file_lock(path: Path):
    """保留锁文件，退出只释放内核锁。"""
    if os.name != "nt":
        raise NotImplementedError("仅支持 Windows 本机")
    import msvcrt

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError("实例操作锁冲突") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def lock_available(path: Path) -> bool:
    """只打开已有锁，不在状态查询时创建目录或文件。"""
    if not path.exists():
        return True
    if os.name != "nt":
        return False
    import msvcrt

    try:
        with path.open("r+b") as handle:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return True
    except OSError:
        return False


class RuntimeEvidence:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "data" / "runtime-state.json"
        self.last_write = 0.0
        self.state = {
            "schema_version": 1,
            "instance_id": read_json(self.root / "instance.json").get("instance_id"),
            "root": str(self.root), "identity": process_identity(os.getpid()),
            "started_at": time.time(), "children": [], "phase": "STARTING",
        }

    def update(self, phase="RUNNING", processes=(), accounts=(), feishu_online=False):
        if phase == self.state["phase"] and time.monotonic() - self.last_write < 2:
            return
        children = [process_identity(p.pid) for p in processes if p.pid]
        self.state.update(phase=phase, heartbeat_at=time.time(),
                          children=[p for p in children if p], accounts=list(accounts),
                          feishu_online=feishu_online)
        atomic_json(self.path, self.state)
        self.last_write = time.monotonic()

    def __enter__(self):
        self.update("STARTING")
        return self

    def __exit__(self, *_):
        # 保留最终子进程证据，管理层仍会逐个核对内核身份。
        self.state.update(phase="STOPPED", heartbeat_at=time.time())
        atomic_json(self.path, self.state)


def evidence(root: Path) -> dict:
    state = read_json(root / "data" / "runtime-state.json")
    metadata = read_json(root / "instance.json")
    owned = state.get("root") == str(root.resolve()) and state.get("instance_id") == metadata.get("instance_id")
    alive = owned and same_process(state.get("identity") or {})
    age = time.time() - state.get("heartbeat_at", 0)
    return {"alive": bool(alive), "heartbeat_fresh": bool(alive and 0 <= age < 15),
            "identity": state.get("identity") if owned else None,
            "children_alive": [p for p in state.get("children", []) if same_process(p)] if owned else [],
            "phase": state.get("phase") if owned else "UNKNOWN",
            "accounts": state.get("accounts", []) if alive else [],
            "feishu_online": bool(alive and state.get("feishu_online"))}
