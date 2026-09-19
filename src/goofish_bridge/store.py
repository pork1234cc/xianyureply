"""主进程独占的业务持久化：固定映射、原子任务和保守恢复。"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

REPLY_INTERVAL_SECONDS = 5


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3):
            raise ValueError("数据库版本不兼容，拒绝降级或覆盖")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
          account_key TEXT PRIMARY KEY, expected_uid TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'STOPPED', last_activity REAL, last_send REAL DEFAULT 0,
          monitor_start REAL NOT NULL, error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS inbox_messages (
          id TEXT PRIMARY KEY, account_key TEXT NOT NULL REFERENCES accounts(account_key),
          account_uid TEXT NOT NULL, cid TEXT NOT NULL, customer_uid TEXT NOT NULL,
          source_message_id TEXT, source_time REAL, received_at REAL NOT NULL,
          customer_name TEXT NOT NULL, text TEXT NOT NULL, message_type TEXT NOT NULL,
          parse_state TEXT NOT NULL, offline INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX IF NOT EXISTS inbox_source ON inbox_messages
          (account_key,account_uid,cid,source_message_id) WHERE source_message_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS feishu_outbox (
          delivery_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
          kind TEXT NOT NULL, inbox_message_id TEXT REFERENCES inbox_messages(id),
          reply_task_id TEXT, target_message_id TEXT, card_key TEXT, text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'QUEUED',
          created_at REAL NOT NULL, feishu_message_id TEXT UNIQUE, error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS reply_tasks (
          task_id TEXT PRIMARY KEY, feishu_reply_message_id TEXT UNIQUE NOT NULL,
          event_id TEXT NOT NULL, account_key TEXT NOT NULL REFERENCES accounts(account_key),
          account_uid TEXT NOT NULL, cid TEXT NOT NULL, customer_uid TEXT NOT NULL,
          text TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
          state TEXT NOT NULL, request_id TEXT, client_uuid TEXT, server_message_id TEXT,
          error TEXT NOT NULL DEFAULT '', card_key TEXT
        );
        CREATE TABLE IF NOT EXISTS feishu_cards (
          card_key TEXT PRIMARY KEY, account_key TEXT NOT NULL REFERENCES accounts(account_key),
          account_uid TEXT NOT NULL, cid TEXT NOT NULL, customer_uid TEXT NOT NULL,
          customer_name TEXT NOT NULL, message_id TEXT UNIQUE, transcript TEXT NOT NULL,
          expanded INTEGER NOT NULL DEFAULT 0,
          updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_state (
          account_key TEXT NOT NULL REFERENCES accounts(account_key), account_uid TEXT NOT NULL,
          cid TEXT NOT NULL, watermark REAL NOT NULL, complete INTEGER NOT NULL DEFAULT 0,
          gap TEXT NOT NULL DEFAULT '', PRIMARY KEY(account_key,account_uid,cid)
        );
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(sync_state)")}
        outbox_columns = {row[1] for row in self.db.execute("PRAGMA table_info(feishu_outbox)")}
        reply_columns = {row[1] for row in self.db.execute("PRAGMA table_info(reply_tasks)")}
        card_columns = {row[1] for row in self.db.execute("PRAGMA table_info(feishu_cards)")}
        with self.db:
            if "checkpoint" not in columns:
                self.db.execute("ALTER TABLE sync_state ADD COLUMN checkpoint REAL NOT NULL DEFAULT 0")
            if "target_message_id" not in outbox_columns:
                self.db.execute("ALTER TABLE feishu_outbox ADD COLUMN target_message_id TEXT")
            if "card_key" not in outbox_columns:
                self.db.execute("ALTER TABLE feishu_outbox ADD COLUMN card_key TEXT")
            if "card_key" not in reply_columns:
                self.db.execute("ALTER TABLE reply_tasks ADD COLUMN card_key TEXT")
            if "expanded" not in card_columns:
                self.db.execute("ALTER TABLE feishu_cards ADD COLUMN expanded INTEGER NOT NULL DEFAULT 0")
            self.db.execute("PRAGMA user_version=3")

    def close(self):
        self.db.close()

    def bind_account(self, key, uid, name, now=None):
        existing = self.db.execute("SELECT expected_uid FROM accounts WHERE account_key=?", (key,)).fetchone()
        if existing and existing[0] != uid:
            raise ValueError("业务库账号 UID 不一致，禁止改绑及发送旧任务")
        with self.db:
            self.db.execute("""INSERT INTO accounts(account_key,expected_uid,name,monitor_start)
              VALUES(?,?,?,?) ON CONFLICT(account_key) DO UPDATE SET name=excluded.name""",
                            (key, uid, name, now if now is not None else time.time()))

    def _notice(self, key, text, kind="NOTICE", inbox=None, reply=None):
        self.db.execute("""INSERT OR IGNORE INTO feishu_outbox
          (delivery_id,idempotency_key,kind,inbox_message_id,reply_task_id,text,created_at)
          VALUES(?,?,?,?,?,?,?)""", (uuid4().hex, key, kind, inbox, reply, text, time.time()))

    def _card_key(self, account_key, account_uid, cid, customer_uid):
        return f"{account_key}:{account_uid}:{cid}:{customer_uid}"

    def _card_payload(self, card):
        return json.dumps({"account_name": card["account_name"],
                           "customer_name": card["customer_name"], "expanded": bool(card.get("expanded")),
                           "entries": card["entries"][-12:] if card.get("expanded") else card["entries"][-4:]},
                          ensure_ascii=False)

    def _queue_card_locked(self, card, inbox_id=None):
        kind = "CUSTOMER_CARD_UPDATE" if card.get("message_id") else "CUSTOMER_CARD"
        payload = self._card_payload(card)
        self.db.execute("""INSERT INTO feishu_outbox
          (delivery_id,idempotency_key,kind,inbox_message_id,target_message_id,card_key,text,created_at)
          VALUES(?,?,?,?,?,?,?,?)""", (uuid4().hex, f"card:{card['card_key']}:{uuid4().hex}", kind,
                                      inbox_id, card.get("message_id"), card["card_key"], payload, time.time()))

    def _reminder_text(self, account_name, customer_name, text):
        return f"闲鱼账号：{account_name}\n客户昵称：{customer_name}\n新消息：{text}"

    def _queue_reminder_locked(self, card_key, inbox_id, account_name, customer_name, text):
        self.db.execute("""INSERT INTO feishu_outbox
          (delivery_id,idempotency_key,kind,inbox_message_id,card_key,text,created_at)
          VALUES(?,?,'CUSTOMER_REMINDER',?,?,?,?)""",
                        (uuid4().hex, f"reminder:{inbox_id}", inbox_id, card_key,
                         self._reminder_text(account_name, customer_name, text), time.time()))

    def _append_card_locked(self, card_key, speaker, text, status=""):
        row = self.db.execute("SELECT * FROM feishu_cards WHERE card_key=?", (card_key,)).fetchone()
        if not row:
            return
        try:
            entries = json.loads(row["transcript"])
        except (TypeError, json.JSONDecodeError):
            entries = []
        entries.append({"speaker": speaker, "text": text, **({"status": status} if status else {})})
        self.db.execute("UPDATE feishu_cards SET transcript=?,updated_at=? WHERE card_key=?",
                        (json.dumps(entries, ensure_ascii=False), time.time(), card_key))

    def notice(self, key, text):
        with self.db:
            self._notice(key, text)

    def account(self, key):
        row = self.db.execute("SELECT * FROM accounts WHERE account_key=?", (key,)).fetchone()
        if not row:
            raise ValueError("账号未初始化")
        return dict(row)

    def state(self, key, state, error=""):
        current = self.account(key)
        with self.db:
            self.db.execute("UPDATE accounts SET state=?,last_activity=?,error=? WHERE account_key=?",
                            (state, time.time(), error, key))
            if current["state"] != state:
                logging.getLogger("goofish_bridge").info("账号 %s 状态 %s，错误类型 %s", key, state, error)
                if state not in {"STARTING", "ONLINE"}:
                    labels = {"RECONNECTING": "正在重连", "AUTH_REQUIRED": "需要重新登录",
                              "RISK_PAUSED": "风控暂停", "STOPPED": "已停止"}
                    label = labels.get(state, state)
                    self._notice(f"state:{key}:{uuid4().hex}",
                                 f"{current['name']}：{label}" + (f"（{error}）" if error else ""))

    def ingest(self, event: dict) -> str | None:
        account = self.account(event["account_key"])
        if event["account_uid"] != account["expected_uid"]:
            raise ValueError("入站账号身份与数据库绑定不符")
        if event.get("customer_uid") == event["account_uid"]:
            return None
        source_time = event.get("source_time")
        if source_time is not None and source_time < account["monitor_start"]:
            return None
        valid = all(event.get(k) for k in ("cid", "customer_uid", "source_message_id"))
        valid = valid and event.get("parse_state") == "OK"
        inbox_id = uuid4().hex
        with self.db:
            cursor = self.db.execute("""INSERT OR IGNORE INTO inbox_messages
            (id,account_key,account_uid,cid,customer_uid,source_message_id,source_time,received_at,
             customer_name,text,message_type,parse_state,offline) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (inbox_id, event["account_key"], event["account_uid"], event.get("cid", ""),
               event.get("customer_uid", ""), event.get("source_message_id") or None,
               source_time, event["received_at"], event.get("customer_name") or "昵称未知",
               event["text"], event["message_type"], "OK" if valid else "ERROR", int(event.get("offline", False))))
            if not cursor.rowcount:
                return None
            customer_name = event.get("customer_name") or "客户"
            if valid:
                card_key = self._card_key(event["account_key"], event["account_uid"],
                                          event["cid"], event["customer_uid"])
                card_row = self.db.execute("SELECT * FROM feishu_cards WHERE card_key=?", (card_key,)).fetchone()
                if card_row:
                    entries = json.loads(card_row["transcript"])
                    message_id = card_row["message_id"]
                else:
                    entries, message_id = [], None
                    self.db.execute("""INSERT INTO feishu_cards
                      (card_key,account_key,account_uid,cid,customer_uid,customer_name,transcript,updated_at)
                      VALUES(?,?,?,?,?,?,?,?)""", (card_key, event["account_key"], event["account_uid"],
                        event["cid"], event["customer_uid"], customer_name, "[]", time.time()))
                entries.append({"speaker": "客户", "text": event["text"]})
                self.db.execute("UPDATE feishu_cards SET customer_name=?,transcript=?,updated_at=? WHERE card_key=?",
                                (customer_name, json.dumps(entries, ensure_ascii=False), time.time(), card_key))
                if card_row:
                    self._queue_reminder_locked(card_key, inbox_id, account["name"], customer_name, event["text"])
                else:
                    self._queue_card_locked({"card_key": card_key, "account_name": account["name"],
                                             "customer_name": customer_name, "entries": entries,
                                             "message_id": message_id, "expanded": 0}, inbox_id)
            else:
                self._notice(f"inbox:{inbox_id}",
                             f"{account['name']}：收到无法验证路由的消息，暂不能回复。", "ANOMALY", inbox_id)
            if valid and source_time is not None:
                self.db.execute("""INSERT INTO sync_state(account_key,account_uid,cid,watermark)
                  VALUES(?,?,?,?) ON CONFLICT(account_key,account_uid,cid)
                  DO UPDATE SET watermark=MAX(watermark,excluded.watermark)""",
                                (event["account_key"], event["account_uid"], event["cid"], source_time))
        return inbox_id

    def receive_reply(self, event: dict, ttl=600, now=None):
        now = time.time() if now is None else now
        previous = self.db.execute("SELECT * FROM reply_tasks WHERE feishu_reply_message_id=?",
                                   (event["message_id"],)).fetchone()
        if previous:
            return dict(previous)
        target = self.db.execute("""SELECT i.* FROM feishu_outbox o JOIN inbox_messages i
           ON i.id=o.inbox_message_id WHERE o.feishu_message_id=? AND o.kind IN ('CUSTOMER','CUSTOMER_CARD','CUSTOMER_REMINDER')
           AND o.state='SERVER_ACCEPTED' AND i.parse_state='OK'""", (event["parent_id"],)).fetchone()
        if not target:
            # 旧 schema 更新可能改为重建卡片；当前卡片映射同样是已确认的固定路由。
            target = self.db.execute("SELECT * FROM feishu_cards WHERE message_id=?",
                                     (event["parent_id"],)).fetchone()
        if not target:
            # 连续回复引用上一条本人消息时，复用已冻结路由，不依赖上一条的发送结果。
            target = self.db.execute("SELECT * FROM reply_tasks WHERE feishu_reply_message_id=?",
                                     (event["parent_id"],)).fetchone()
        if not target:
            self.notice(f"reject:{event['message_id']}", "无法找到直接引用的原客户消息，未发送。请引用机器人转发的原客户消息。")
            return None
        account = self.account(target["account_key"])
        if target["account_uid"] != account["expected_uid"]:
            raise ValueError("引用目标账号绑定已变更，拒绝发送")
        created = event["create_time"] / 1000
        if created > now + 30:
            self.notice(f"reject:{event['message_id']}", "消息时间异常，未发送，请重新引用提交。")
            return None
        state = "EXPIRED" if created + ttl <= now else "QUEUED"
        task = uuid4().hex
        card = self.db.execute("""SELECT card_key FROM feishu_cards
          WHERE account_key=? AND account_uid=? AND cid=? AND customer_uid=?""",
                              (target["account_key"], target["account_uid"], target["cid"], target["customer_uid"])).fetchone()
        with self.db:
            self.db.execute("""INSERT INTO reply_tasks
              (task_id,feishu_reply_message_id,event_id,account_key,account_uid,cid,customer_uid,
               text,created_at,expires_at,state,card_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (task, event["message_id"], event.get("event_id", ""), target["account_key"],
                             target["account_uid"], target["cid"], target["customer_uid"], event["text"],
                             created, created + ttl, state, card["card_key"] if card else None))
            if state != "QUEUED":
                self._receipt(task, state)
        return self.task(task)

    def receive_card_reply(self, event, ttl=600, now=None):
        """通过卡片消息 ID 定位客户，不要求引用消息。"""
        now = time.time() if now is None else now
        text = (event.get("text") or "").strip()
        if not text or len(text) > 2000:
            self.notice(f"card-reject:{event['message_id']}", "回复内容为空或超过 2000 字符，未发送。")
            return None
        previous = self.db.execute("SELECT * FROM reply_tasks WHERE feishu_reply_message_id=?",
                                   (event["message_id"],)).fetchone()
        if previous:
            return dict(previous)
        card = self.db.execute("SELECT * FROM feishu_cards WHERE message_id=?", (event["card_message_id"],)).fetchone()
        if not card:
            self.notice(f"card-reject:{event['message_id']}", "卡片已失效，请等待新的客户消息卡片。")
            return None
        account = self.account(card["account_key"])
        if card["account_uid"] != account["expected_uid"]:
            raise ValueError("卡片目标账号绑定已变更，拒绝发送")
        created = event.get("create_time", now)
        state = "EXPIRED" if created + ttl <= now else "QUEUED"
        task = uuid4().hex
        with self.db:
            self.db.execute("""INSERT INTO reply_tasks
              (task_id,feishu_reply_message_id,event_id,account_key,account_uid,cid,customer_uid,
               text,created_at,expires_at,state,card_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (task, event["message_id"], event.get("event_id", ""), card["account_key"],
                             card["account_uid"], card["cid"], card["customer_uid"], text,
                             created, created + ttl, state, card["card_key"]))
            if state != "QUEUED":
                self._append_card_locked(card["card_key"], "系统", text, "已过期，未发送")
                self._receipt(task, state)
        return self.task(task)

    def set_card_view(self, card_message_id, expanded):
        """切换卡片展示范围，不改变本地完整会话记录。"""
        with self.db:
            row = self.db.execute("SELECT * FROM feishu_cards WHERE message_id=?", (card_message_id,)).fetchone()
            if not row:
                return False
            value = int(bool(expanded))
            self.db.execute("UPDATE feishu_cards SET expanded=?,updated_at=? WHERE card_key=?",
                            (value, time.time(), row["card_key"]))
            self._queue_card_locked({"card_key": row["card_key"],
                                     "account_name": self.account(row["account_key"])["name"],
                                     "customer_name": row["customer_name"],
                                     "entries": json.loads(row["transcript"]),
                                     "message_id": row["message_id"], "expanded": value})
        return True

    def task(self, task):
        return dict(self.db.execute("SELECT * FROM reply_tasks WHERE task_id=?", (task,)).fetchone())

    def _receipt(self, task, state):
        # 正常排队和发送成功只记本地，避免逐句回执打断详情页中的连续回复。
        if state not in {"FAILED", "UNKNOWN", "EXPIRED"}:
            return
        row = self.task(task)
        labels = {"FAILED": "发送失败", "UNKNOWN": "结果待确认",
                  "EXPIRED": "已过期，未发送"}
        card = self.db.execute("SELECT customer_name FROM feishu_cards WHERE card_key=?",
                               (row["card_key"],)).fetchone()
        prefix = (f"闲鱼账号：{self.account(row['account_key'])['name']}\n"
                  f"客户昵称：{card['customer_name'] if card else '客户'}\n")
        self._notice(f"receipt:{task}:{state}",
                     f"{prefix}回复：{row['text']}\n状态：{labels[state]}",
                     "RECEIPT", reply=task)

    def recover(self):
        with self.db:
            for row in self.db.execute("SELECT task_id FROM reply_tasks WHERE state='DISPATCHING'").fetchall():
                self.db.execute("UPDATE reply_tasks SET state='UNKNOWN' WHERE task_id=?", (row[0],))
                self._receipt(row[0], "UNKNOWN")
            self.db.execute("UPDATE feishu_outbox SET state='UNKNOWN' WHERE state='DISPATCHING'")
            # 保留旧回执记录，但升级后不再投递尚未发出的正常状态通知。
            self.db.execute("""UPDATE feishu_outbox SET state='SUPPRESSED',error='正常回复状态已改为静默'
              WHERE state='QUEUED' AND kind='RECEIPT'
                AND (idempotency_key LIKE 'receipt:%:SERVER_ACCEPTED'
                     OR idempotency_key LIKE 'receipt:%:QUEUED')""")
            # 历史同步告警只保存在本地日志，不再向飞书展示；清理旧版本遗留的待发送提示。
            self.db.execute("""UPDATE feishu_outbox SET state='FAILED', error='历史同步提示已按展示策略抑制'
               WHERE state='QUEUED' AND kind='NOTICE'
                 AND (text LIKE '%历史%' OR text LIKE '%补拉%' OR text LIKE '%会话%')""")
            # 重启不刷新现有卡片，防止客户端未提交草稿被覆盖。
            self.db.execute("""UPDATE feishu_outbox SET state='FAILED',error='重启后请主动刷新卡片'
              WHERE kind='CUSTOMER_CARD_UPDATE' AND inbox_message_id IS NULL AND state='QUEUED'""")
            # 只恢复明确失败的首次建卡；未知投递不重发，避免重复建卡。
            for row in self.db.execute("SELECT * FROM feishu_cards WHERE message_id IS NULL").fetchall():
                pending = self.db.execute("""SELECT 1 FROM feishu_outbox
                  WHERE card_key=? AND kind IN ('CUSTOMER_CARD','CUSTOMER_CARD_UPDATE')
                    AND state IN ('QUEUED','DISPATCHING','UNKNOWN','SERVER_ACCEPTED') LIMIT 1""", (row["card_key"],)).fetchone()
                failed = self.db.execute("""SELECT inbox_message_id FROM feishu_outbox
                  WHERE card_key=? AND kind='CUSTOMER_CARD' AND state='FAILED'
                  ORDER BY created_at,rowid LIMIT 1""", (row["card_key"],)).fetchone()
                if not pending and failed:
                    self._queue_card_locked({"card_key": row["card_key"],
                                             "account_name": self.account(row["account_key"])["name"],
                                             "customer_name": row["customer_name"],
                                             "entries": json.loads(row["transcript"]),
                                             "message_id": row["message_id"], "expanded": row["expanded"]},
                                            failed["inbox_message_id"])
            self.db.execute("UPDATE accounts SET state='STOPPED'")

    def expire(self, now=None):
        now = time.time() if now is None else now
        with self.db:
            for row in self.db.execute("SELECT task_id FROM reply_tasks WHERE state='QUEUED' AND expires_at<=?", (now,)).fetchall():
                self.db.execute("UPDATE reply_tasks SET state='EXPIRED' WHERE task_id=?", (row[0],))
                self._receipt(row[0], "EXPIRED")

    def claim_reply(self, key, request_id, client_uuid, now=None):
        now = time.time() if now is None else now
        account = self.account(key)
        if account["state"] != "ONLINE" or now - account["last_send"] < REPLY_INTERVAL_SECONDS:
            return None
        busy = self.db.execute("SELECT 1 FROM reply_tasks WHERE account_key=? AND state='DISPATCHING'", (key,)).fetchone()
        if busy:
            return None
        row = self.db.execute("""SELECT * FROM reply_tasks WHERE account_key=? AND account_uid=?
           AND state='QUEUED' AND expires_at>? ORDER BY created_at,rowid LIMIT 1""",
                              (key, account["expected_uid"], now)).fetchone()
        if not row:
            return None
        with self.db:
            self.db.execute("UPDATE reply_tasks SET state='DISPATCHING',request_id=?,client_uuid=? WHERE task_id=?",
                            (request_id, client_uuid, row["task_id"]))
            self.db.execute("UPDATE accounts SET last_send=? WHERE account_key=?", (now, key))
        return self.task(row["task_id"])

    def finish_reply(self, task, state, message_id="", error=""):
        if state not in {"SERVER_ACCEPTED", "UNKNOWN", "FAILED", "EXPIRED"}:
            raise ValueError("无效回复终态")
        with self.db:
            before = self.db.execute("SELECT * FROM reply_tasks WHERE task_id=? AND state='DISPATCHING'",
                                    (task,)).fetchone()
            cursor = self.db.execute("""UPDATE reply_tasks SET state=?,server_message_id=?,error=?
               WHERE task_id=? AND state='DISPATCHING'""", (state, message_id, error, task))
            if cursor.rowcount:
                if before and before["card_key"]:
                    labels = {"SERVER_ACCEPTED": "发送成功", "FAILED": "发送失败",
                              "UNKNOWN": "结果待确认", "EXPIRED": "已过期，未发送"}
                    self._append_card_locked(before["card_key"], "我方", before["text"], labels[state])
                self._receipt(task, state)
                logging.getLogger("goofish_bridge").info("回复任务终态：%s", state)

    def claim_outbox(self):
        row = self.db.execute("""SELECT o.* FROM feishu_outbox o
          LEFT JOIN feishu_cards c ON c.card_key=o.card_key
          WHERE o.state='QUEUED' AND (o.kind!='CUSTOMER_REMINDER' OR c.message_id IS NOT NULL)
          ORDER BY o.created_at,o.rowid LIMIT 1""").fetchone()
        if not row:
            return None
        delivery = dict(row)
        with self.db:
            card = self.db.execute("SELECT * FROM feishu_cards WHERE card_key=?",
                                   (row["card_key"],)).fetchone()
            # 兼容升级前排队的入站卡片更新及重复建卡，投递时固定引用当前原卡片。
            if card and card["message_id"] and row["inbox_message_id"] and row["kind"] in {
                    "CUSTOMER_CARD", "CUSTOMER_CARD_UPDATE", "CUSTOMER_REMINDER"}:
                inbox = self.db.execute("SELECT * FROM inbox_messages WHERE id=?",
                                        (row["inbox_message_id"],)).fetchone()
                delivery.update(kind="CUSTOMER_REMINDER", target_message_id=card["message_id"],
                                text=self._reminder_text(self.account(inbox["account_key"])["name"],
                                                         inbox["customer_name"], inbox["text"]))
                self.db.execute("UPDATE feishu_outbox SET kind=?,target_message_id=?,text=? WHERE delivery_id=?",
                                (delivery["kind"], delivery["target_message_id"], delivery["text"], row["delivery_id"]))
            self.db.execute("UPDATE feishu_outbox SET state='DISPATCHING' WHERE delivery_id=?", (row["delivery_id"],))
        return delivery

    def finish_outbox(self, delivery, state, message_id=None, error=""):
        if state not in {"SERVER_ACCEPTED", "UNKNOWN", "FAILED"}:
            raise ValueError("无效飞书终态")
        with self.db:
            row = self.db.execute("SELECT * FROM feishu_outbox WHERE delivery_id=? AND state='DISPATCHING'",
                                  (delivery,)).fetchone()
            if state == "SERVER_ACCEPTED" and not message_id and not (
                    row and row["kind"] == "CUSTOMER_CARD_UPDATE" and row["target_message_id"]):
                raise ValueError("飞书接受回包必须包含 message_id")
            stored_message_id = None if row and row["kind"] == "CUSTOMER_CARD_UPDATE" else (
                message_id or (row["target_message_id"] if row and row["kind"] == "CUSTOMER_CARD" else None))
            self.db.execute("""UPDATE feishu_outbox SET state=?,feishu_message_id=?,error=?
               WHERE delivery_id=? AND state='DISPATCHING'""",
                             (state, stored_message_id, error, delivery))
            if row and row["kind"] in {"CUSTOMER_CARD", "CUSTOMER_CARD_UPDATE"} and state == "SERVER_ACCEPTED":
                current_message_id = message_id or row["target_message_id"]
                self.db.execute("UPDATE feishu_cards SET message_id=?,updated_at=? WHERE card_key=?",
                                (current_message_id, time.time(), row["card_key"]))

    def sync_positions(self, key):
        return [dict(r) for r in self.db.execute("SELECT * FROM sync_state WHERE account_key=?", (key,))]

    def sync_result(self, key, uid, cid, complete, gap):
        account = self.account(key)
        with self.db:
            self.db.execute("""INSERT INTO sync_state(account_key,account_uid,cid,watermark,complete,gap)
              VALUES(?,?,?,?,?,?) ON CONFLICT(account_key,account_uid,cid)
              DO UPDATE SET complete=excluded.complete,gap=excluded.gap""",
                            (key, uid, cid, account["monitor_start"], int(complete), gap))
            if complete:
                self.db.execute("UPDATE sync_state SET checkpoint=watermark WHERE account_key=? AND account_uid=? AND cid=?",
                                (key, uid, cid))
            recent_notice = self.db.execute(
                "SELECT 1 FROM feishu_outbox WHERE idempotency_key LIKE ? AND created_at>? LIMIT 1",
                (f"gap:{key}:%", time.time() - 60),
            ).fetchone()
            if gap and not recent_notice:
                logging.getLogger("goofish_bridge").warning(
                    "账号 %s 历史同步未完整：%s", account["name"], gap)

    def status_text(self):
        accounts = [f"{r['account_key']}：{r['state']}" for r in self.db.execute("SELECT * FROM accounts ORDER BY account_key")]
        pending = self.db.execute("SELECT count(*) FROM reply_tasks WHERE state IN ('QUEUED','DISPATCHING')").fetchone()[0]
        errors = self.db.execute("SELECT count(*) FROM reply_tasks WHERE state IN ('UNKNOWN','FAILED','EXPIRED')").fetchone()[0]
        unknown_outbox = self.db.execute("SELECT count(*) FROM feishu_outbox WHERE state IN ('UNKNOWN','FAILED')").fetchone()[0]
        return "\n".join(accounts + [f"待发送回复：{pending}", f"异常回复：{errors}", f"异常飞书投递：{unknown_outbox}"])
