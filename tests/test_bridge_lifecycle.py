"""真实内核身份、陈旧业务状态和只读锁检查。"""

import os
import time

import pytest

from goofish_bridge.lifecycle import (
    RuntimeEvidence,
    atomic_json,
    evidence,
    file_lock,
    lock_available,
    process_identity,
    same_process,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows 内核进程证据")


def test_pid_reuse_and_stale_heartbeat(tmp_path):
    atomic_json(tmp_path / "instance.json", {"instance_id": "test"})
    with RuntimeEvidence(tmp_path) as writer:
        writer.update(accounts=[{"key": "A1", "state": "ONLINE"}])
        assert evidence(tmp_path)["alive"]
        assert evidence(tmp_path)["heartbeat_fresh"]
        writer.state["heartbeat_at"] = time.time() - 60
        atomic_json(writer.path, writer.state)
        assert not evidence(tmp_path)["heartbeat_fresh"]
        writer.state["identity"]["created"] += 1
        atomic_json(writer.path, writer.state)
        assert not evidence(tmp_path)["alive"]
        assert evidence(tmp_path)["accounts"] == []


def test_lock_and_no_read_side_effect(tmp_path):
    lock = tmp_path / "data" / "bridge.lock"
    assert lock_available(lock)
    assert not lock.parent.exists()
    with file_lock(lock):
        assert not lock_available(lock)
        with pytest.raises(BlockingIOError), file_lock(lock):
            pass
    assert lock_available(lock)


def test_instance_mismatch(tmp_path):
    with RuntimeEvidence(tmp_path):
        atomic_json(tmp_path / "instance.json", {"instance_id": "different"})
        assert not evidence(tmp_path)["alive"]
    identity = process_identity(os.getpid())
    assert same_process(identity)
    assert not same_process({"pid": identity["pid"], "created": 0})
