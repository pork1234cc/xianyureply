"""Windows spawn 进程监督与主进程唯一数据库写入循环。"""

from __future__ import annotations

import logging
import multiprocessing
import queue
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from goofish_bridge.account import AccountPaths, account_lock, bound_uid, validate_uid
from goofish_bridge.account_worker import worker_main
from goofish_bridge.feishu_adapter import FeishuRuntime
from goofish_bridge.router import route_card_operator, route_operator
from goofish_bridge.store import Store
from goofish_cli.core.sign import generate_mid, generate_uuid


def handle_worker_event(store, event, channels, test_customer=None):
    key, uid, payload = event["account"], event["uid"], event["payload"]
    if store.account(key)["expected_uid"] != uid:
        raise ValueError("子进程账号 UID 与主进程绑定冲突")
    kind = event["kind"]
    if kind == "incoming":
        if payload.get("account_key") != key or payload.get("account_uid") != uid:
            raise ValueError("子进程事件信封与消息账号不一致")
        if not test_customer or payload.get("customer_uid") == test_customer:
            store.ingest(payload)
    elif kind == "state":
        store.state(key, payload["state"], payload.get("error", ""))
    elif kind == "notice":
        account = store.account(key)
        text = str(payload)
        if any(word in text for word in ("补拉", "历史", "会话", "上限", "缺口")):
            # 历史同步告警只写日志，避免在飞书对话框制造运行噪声。
            logging.getLogger("goofish_bridge").warning("账号 %s 历史同步提示：%s", account["name"], text)
        else:
            store.notice(f"worker:{key}:{event['id']}", f"{account['name']}：{text}")
    elif kind == "sync":
        store.sync_result(key, uid, payload["cid"], payload["complete"], payload["gap"])
    elif kind == "result":
        task = store.task(payload["task_id"])
        if task["account_key"] != key or task["account_uid"] != uid:
            raise ValueError("结果事件不属于该账号任务")
        store.finish_reply(payload["task_id"], payload["state"], payload["message_id"], payload["error"])
    else:
        raise ValueError("未知子进程事件类型")
    if event["durable"]:
        channels[key].put({"kind": "committed", "id": event["id"]}, timeout=3)


def run(config, config_path: Path, keys, stop_file="data/stop.request", test_customer=None, duration=None):
    bridge = config.raw["bridge"]
    if (bridge.get("auto_retry_unknown_send") is not False
            or bridge.get("reply_mode") != "direct_parent_only"
            or bridge.get("write_rpm_per_account") != 1):
        raise ValueError("首版要求只接受直接引用、每账号每分钟 1 次、未知不重发")
    # 单实例锁复用文件锁机制；锁文件位于已受 ACL 保护的账号父目录内。
    lock_paths = AccountPaths(config.root, "A1")
    lock_paths.directory.mkdir(parents=True, exist_ok=True)
    instance_paths = _InstancePaths(config.root / "data")
    with account_lock(instance_paths):
        return _run_locked(config, config_path, keys, stop_file, test_customer, duration)


class _InstancePaths:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def file(self, name):
        return self.directory / "bridge.lock"


def _run_locked(config, config_path, keys, stop_file, test_customer, duration):
    bridge = config.raw["bridge"]
    ctx = multiprocessing.get_context("spawn")
    events = ctx.Queue(maxsize=512)
    local_events = queue.Queue(maxsize=256)
    shutdown = ctx.Event()
    channels, processes = {}, {}
    restart_counts = dict.fromkeys(keys, 0)
    db_path = config.root / config.raw["app"]["database"]
    if test_customer:
        db_path = config.root / "data" / "bridge-test.sqlite"
    store = Store(db_path.resolve())
    runtime = None
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="feishu-send")
    sending = None
    started = time.time()
    online_notice_sent = False
    stop_path = (config.root / stop_file).resolve()
    old_stop_mtime = stop_path.stat().st_mtime if stop_path.exists() else 0
    try:
        for key in keys:
            paths = AccountPaths(config.root, key)
            uid = bound_uid(paths)
            validate_uid(config, paths, uid)
            store.bind_account(key, uid, config.account(key)["name"])
        store.recover()
        runtime = FeishuRuntime(config, local_events)
        runtime.start()

        def start_worker(key):
            channels[key] = ctx.Queue(maxsize=128)
            initial = {"monitor_start": store.account(key)["monitor_start"], "positions": store.sync_positions(key)}
            process = ctx.Process(target=worker_main, name=f"goofish-{key}",
                                  args=(str(config_path.resolve()), key, channels[key], events, shutdown, initial))
            process.start()
            processes[key] = process

        for key in keys:
            start_worker(key)
        print(f"消息桥已启动：{', '.join(keys)}；请等待账号 ONLINE。按 Ctrl+C 安全退出。", flush=True)
        while True:
            if duration and time.time() - started >= duration:
                break
            if stop_path.exists() and stop_path.stat().st_mtime > old_stop_mtime:
                break
            for _ in range(100):
                try:
                    item = events.get_nowait()
                except queue.Empty:
                    break
                handle_worker_event(store, item, channels, test_customer)
            for _ in range(100):
                try:
                    local = local_events.get_nowait()
                except queue.Empty:
                    break
                if local["kind"] == "operator":
                    route_operator(store, local["raw"], runtime.binding, ttl=bridge.get("reply_ttl_seconds", 600))
                    local["committed"].set()
                elif local["kind"] == "card":
                    route_card_operator(store, local["raw"], runtime.binding,
                                        ttl=bridge.get("reply_ttl_seconds", 600))
                    local["committed"].set()
                elif local["kind"] == "feishu_error":
                    print(f"飞书连接异常：{local['error']}。", flush=True)
            if not online_notice_sent and all(store.account(key)["state"] == "ONLINE" for key in keys):
                names = "、".join(store.account(key)["name"] for key in keys)
                store.notice(f"runtime:online:{int(started)}", f"消息桥已就绪：{names}在线。")
                online_notice_sent = True
            store.expire()
            for key, process in list(processes.items()):
                if process.exitcode is not None:
                    for task in store.db.execute("SELECT task_id FROM reply_tasks WHERE account_key=? AND state='DISPATCHING'", (key,)).fetchall():
                        store.finish_reply(task[0], "UNKNOWN", error="账号进程退出")
                    store.state(key, "STOPPED", "账号进程退出")
                    if restart_counts[key] < 3:
                        restart_counts[key] += 1
                        start_worker(key)
                    else:
                        processes.pop(key)
                    continue
                candidate = store.db.execute("""SELECT 1 FROM reply_tasks WHERE account_key=?
                  AND state='QUEUED' AND expires_at>? LIMIT 1""", (key, time.time())).fetchone()
                account = store.account(key)
                if candidate and account["state"] == "ONLINE" and time.time() - account["last_send"] >= 60:
                    task = store.claim_reply(key, generate_mid(), generate_uuid())
                    if task:
                        if test_customer and task["customer_uid"] != test_customer:
                            store.finish_reply(task["task_id"], "FAILED", error="测试模式禁止其他客户")
                        else:
                            channels[key].put({"kind": "send", "task": task}, timeout=3)
            if sending and sending[0].done():
                state, message_id, error = sending[0].result()
                store.finish_outbox(sending[1], state, message_id, error)
                sending = None
            if sending is None and runtime.online.is_set():
                delivery = store.claim_outbox()
                if delivery:
                    sending = (executor.submit(runtime.send, delivery), delivery["delivery_id"])
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("正在停止账号进程并保存任务状态……", flush=True)
    finally:
        shutdown.set()
        deadline = time.monotonic() + 20
        while any(p.is_alive() for p in processes.values()) and time.monotonic() < deadline:
            try:
                item = events.get(timeout=0.2)
                handle_worker_event(store, item, channels, test_customer)
            except queue.Empty:
                pass
            except (sqlite3.Error, OSError, ValueError, queue.Full):
                # 持久化失败也必须继续关闭已启动进程，不能因清理阶段再次写库失败而遗留发送者。
                break
        for process in processes.values():
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        if sending:
            state, message_id, error = sending[0].result(timeout=35)
            store.finish_outbox(sending[1], state, message_id, error)
        executor.shutdown(wait=True)
        if runtime:
            runtime.close()
        try:
            store.recover()
            print(store.status_text(), flush=True)
        finally:
            store.close()
