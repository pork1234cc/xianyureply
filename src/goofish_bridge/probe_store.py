"""阶段 0 专用诊断记录，与未来正式业务数据库分离。"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_SCHEMA_KEYS = set(["apiName", "app-key", "app_id", "arg1", "argInfo", "args", "arouseChatScriptInfo", "arouseTimeStamp", "bizTag", "bizType", "body", "channel", "chatGuidance", "chatGuidanceIcon", "chatScrip", "chatScripStrategy", "chatType", "chat_id", "cid", "clientIp", "code", "content", "contentType", "cookie", "createAt", "createTime", "create_time", "custom", "data", "decoded", "degrade", "degradeFailover", "detailNotice", "displayStyle", "dt", "endSeq", "extJson", "extUserId", "extUserType", "extension", "extensions", "failover", "fingerprint", "frame", "func_name", "functionBaseName", "functionIcon", "functionName", "functionUrl", "groupOwnerId", "hasMore", "headers", "icon", "incrementType", "intent", "intentType", "ip-digest", "ip-region-digest", "isFromChina", "itemFeatures", "itemId", "itemMainPic", "itemSellerId", "itemTitle", "lwp", "maxHighPts", "maxPts", "memberFlags", "message", "messageId", "message_id", "mid", "minCreateTime", "msgReadStatusDowngrade", "msgReadStatusSetting", "msgStatus", "mtop", "needKeyBoard", "needPush", "nextCursor", "objectType", "open_id", "operation", "operatorType", "operatorUid", "orderId", "ownerUserId", "ownerUserType", "params", "parent_id", "port", "readStatus", "real-ip", "recallFeature", "receiverCount", "receiverIds", "receivers", "reconnectType", "redPointPolicy", "reg-sid", "reg-uid", "reminderContent", "reminderNotice", "reminderTitle", "reminderUrl", "resident", "residentFunctions", "sample", "searchableContent", "sender", "senderUserId", "senderUserType", "server-timestamp", "sessionArouse", "sessionArouseInfo", "sessionId", "sessionInfo", "sessionType", "session_id", "showRecallStatusSetting", "sid", "startSeq", "streamId", "summary", "syncExtensionModel", "syncExtraType", "syncId", "syncPushPackage", "tag", "tenant_key", "text", "timeStamp", "timestamp", "title", "topic_title", "txt", "type", "ua", "uid", "umid", "umidToken", "unitName", "unreadCount", "userExtension", "userMessageModels", "utdid", "uuid", "version", "_appVersion", "_platform", "nested", "user"])


class ProbeStore:
    def __init__(self, root: Path):
        (root / "data").mkdir(exist_ok=True)
        self.connection = sqlite3.connect(root / "data" / "probe.sqlite")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS probe_events (
                id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL,
                channel TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS probe_sends (
                id TEXT PRIMARY KEY, account_key TEXT NOT NULL, account_uid TEXT NOT NULL,
                cid TEXT NOT NULL, customer_uid TEXT NOT NULL, created_at TEXT NOT NULL,
                state TEXT NOT NULL, request_id TEXT, client_uuid TEXT,
                server_message_id TEXT, error_type TEXT
            );
        """)
        self.salt = secrets.token_bytes(32)

    def close(self):
        self.connection.close()

    def redact(self, value):
        """保持字段路径和同值关联，不把 UID、正文、Token 或未知字段值写入样本。"""
        if isinstance(value, dict):
            return {self.safe_key(str(k)): self.redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if value is None or isinstance(value, bool):
            return value
        digest = hashlib.sha256(self.salt + str(value).encode("utf-8")).hexdigest()[:16]
        return f"<{type(value).__name__}:{digest}>"

    def safe_key(self, key: str) -> str:
        if key in _SCHEMA_KEYS or (key.isascii() and key.isdigit() and 0 <= int(key) <= 99):
            return key
        digest = hashlib.sha256(self.salt + key.encode("utf-8")).hexdigest()[:16]
        return f"<key:{digest}>"

    def redact_keys(self, value):
        """导出时再次校验旧诊断记录中的动态字段名，避免 UID 藏在 key 内。"""
        if isinstance(value, dict):
            return {self.safe_key(str(k)): self.redact_keys(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact_keys(v) for v in value]
        return value

    def event(self, channel: str, value: dict):
        payload = json.dumps(self.redact(value), ensure_ascii=False)
        with self.connection:
            self.connection.execute(
                "INSERT INTO probe_events(recorded_at,channel,payload) VALUES(?,?,?)",
                (datetime.now(UTC).isoformat(), channel, payload),
            )

    def prepare_send(self, account_key: str, account_uid: str, cid: str, customer_uid: str,
                     request_id: str, client_uuid: str) -> str:
        task_id = uuid4().hex
        with self.connection:
            self.connection.execute(
                """INSERT INTO probe_sends
                (id,account_key,account_uid,cid,customer_uid,created_at,state,request_id,client_uuid)
                VALUES(?,?,?,?,?,?,'DISPATCHING',?,?)""",
                (task_id, account_key, account_uid, cid, customer_uid,
                 datetime.now(UTC).isoformat(), request_id, client_uuid),
            )
        return task_id

    def finish_send(self, task_id: str, state: str, server_message_id="", error_type=""):
        if state not in {"SERVER_ACCEPTED", "FAILED", "UNKNOWN"}:
            raise ValueError("无效发送状态")
        with self.connection:
            self.connection.execute(
                """UPDATE probe_sends SET state=?,server_message_id=?,error_type=?
                WHERE id=? AND state='DISPATCHING'""",
                (state, server_message_id, error_type, task_id),
            )

    def recover_account(self, account_key: str):
        # 调用方已持有账号锁，不会将另一活跃进程的任务错误标记为崩溃。
        with self.connection:
            self.connection.execute(
                "UPDATE probe_sends SET state='UNKNOWN' WHERE account_key=? AND state='DISPATCHING'",
                (account_key,),
            )

    def export(self, destination: Path):
        # 使用独占新建避免覆盖用户已有样本。
        with destination.open("x", encoding="utf-8") as handle:
            for channel, payload in self.connection.execute(
                "SELECT channel,payload FROM probe_events ORDER BY id"
            ):
                handle.write(json.dumps({"channel": channel,
                                         "sample": self.redact_keys(json.loads(payload))},
                                        ensure_ascii=False) + "\n")
