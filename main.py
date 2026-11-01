from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import astrbot.api.star as star
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import File, Plain
from astrbot.core.agent.message import TextPart
from quart import jsonify, request

PLUGIN_NAME = "couple_ai"
DEFAULT_INJECT = 5


def _now() -> float:
    return time.time()


def _short(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _to_ts(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return _now()
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


class CoupleAIPlugin(star.Star):
    """情侣 AI：跨房间任务 + 共享事件 + 水印 + 日记。"""

    def __init__(self, context: star.Context, config: Any) -> None:
        super().__init__(context)
        self.config = config or {}
        self.data_dir: Path = star.StarTools.get_data_dir(PLUGIN_NAME)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "diaries").mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "couple_ai.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._stop = False
        self._task: asyncio.Task | None = None
        self._init_db()
        self._register_web_apis()
        logger.info("[couple_ai] 插件已创建")

    # ── 配置辅助 ──────────────────────────────────────────────────────────
    def _cfg(self, key: str, default: Any) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            value = default
        return default if value is None else value

    @property
    def enabled(self) -> bool:
        return bool(self._cfg("enabled", True))

    def _labels(self) -> dict[str, str]:
        raw = str(self._cfg("session_labels", "") or "")
        result: dict[str, str] = {}
        for line in raw.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and value.strip():
                result[key.strip()] = value.strip()
        return result

    def _label(self, umo: str) -> str:
        labels = self._labels()
        if umo in labels:
            return labels[umo]
        parts = str(umo).split(":")
        if len(parts) >= 3:
            kind, sid = parts[1], parts[2]
            return ("群 " if "Group" in kind else "私聊 ") + sid
        return str(umo)

    def _allow(self, umo: str) -> bool:
        raw = str(self._cfg("allow_sessions", "") or "").strip()
        if not raw:
            return True
        allowed = {line.strip() for line in raw.splitlines() if line.strip()}
        return umo in allowed

    # ── 数据库 ────────────────────────────────────────────────────────────
    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    umo TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'say',
                    text TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    from_umo TEXT NOT NULL,
                    to_umo TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'notify',
                    text TEXT NOT NULL,
                    due_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    sent_at REAL,
                    done_at REAL,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL,
                    mode TEXT NOT NULL DEFAULT 'target_voice'
                );
                CREATE TABLE IF NOT EXISTS event_seen (
                    umo TEXT PRIMARY KEY,
                    last_id INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS diaries (
                    date TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sent_to TEXT
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_at);
                CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
                """
            )
            existing = {
                row[1] for row in self._conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            for column, ddl in (
                ("attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("next_attempt_at", "REAL"),
                ("mode", "TEXT NOT NULL DEFAULT 'target_voice'"),
            ):
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {ddl}")
            self._conn.commit()

    # ── 事件 ─────────────────────────────────────────────────────────────
    def _add_event(self, umo: str, kind: str, text: str) -> None:
        if not self._cfg("event_log_enabled", True):
            return
        limit = max(1, int(self._cfg("event_max_chars", 80)))
        clean = _short(text, limit)
        if not clean:
            return
        keep = max(20, int(self._cfg("event_keep", 200)))
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(ts, umo, kind, text) VALUES(?,?,?,?)",
                (_now(), umo, kind, clean),
            )
            self._conn.execute(
                "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
                (keep,),
            )
            self._conn.commit()

    def _watermark(self, umo: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_id FROM event_seen WHERE umo=?", (umo,)
            ).fetchone()
        return int(row[0]) if row else 0

    def _set_watermark(self, umo: str, last_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO event_seen(umo,last_id) VALUES(?,?) "
                "ON CONFLICT(umo) DO UPDATE SET last_id=excluded.last_id",
                (umo, int(last_id)),
            )
            self._conn.commit()

    def _events_block(self, umo: str) -> tuple[str, int]:
        count = int(self._cfg("event_inject_count", DEFAULT_INJECT))
        if count <= 0:
            return "", 0
        last_id = self._watermark(umo)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, umo, kind, text FROM events "
                "WHERE id > ? AND umo != ? ORDER BY id ASC LIMIT ?",
                (last_id, umo, max(1, count)),
            ).fetchall()
        if not rows:
            return "", 0
        lines = []
        for row in rows:
            stamp = datetime.fromtimestamp(row["ts"]).strftime("%H:%M")
            action = {"say": "说了", "task": "把话带给了", "diary": "写了日记"}.get(
                row["kind"], "做了"
            )
            lines.append(f"- {stamp} 我在 {self._label(row['umo'])} {action}：{row['text']}")
        max_id = max(int(row["id"]) for row in rows)
        block = (
            "【我最近在别处】\n"
            + "\n".join(lines)
            + "\n（这些是你自己在别的会话里做过的事，不是别人说的；保持同一个人，不要把它们当成新任务。）"
        )
        return block, max_id

    # ── 元数据 ───────────────────────────────────────────────────────────
    def _meta_get(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def _meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    # ── 任务 ─────────────────────────────────────────────────────────────
    def _task_row(self, task_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def _create_task(
        self,
        from_umo: str,
        to_umo: str,
        text: str,
        due_at: str | None,
        kind: str,
    ) -> tuple[dict | None, str]:
        if not self.enabled:
            return None, "插件未启用"
        if not to_umo or ":" not in to_umo:
            return None, "目标会话 UMO 格式应为 platform:MessageType:session_id"
        if to_umo == from_umo:
            return None, "不能给自己创建跨房间任务"
        if not self._allow(to_umo):
            return None, f"目标会话不在 allow_sessions 白名单：{to_umo}"
        limit = max(1, int(self._cfg("task_max_chars", 200)))
        clean = str(text or "").strip()
        if not clean:
            return None, "任务内容不能为空"
        if len(clean) > limit:
            return None, f"任务内容不能超过 {limit} 字"
        due = _to_ts(due_at)
        if due is None:
            return None, "due_at 不是合法时间（ISO 8601 或时间戳）"
        max_pending = max(1, int(self._cfg("task_max_pending", 20)))
        with self._lock:
            pending = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE to_umo=? AND status IN ('pending','dispatching')",
                (to_umo,),
            ).fetchone()[0]
        if pending >= max_pending:
            return None, "目标会话待处理任务过多，稍后再试"
        task_id = "t-" + uuid.uuid4().hex[:10]
        use_mode = "target_voice"
        with self._lock:
            self._conn.execute(
                """INSERT INTO tasks(id, from_umo, to_umo, kind, text, due_at, status, created_at, attempts, next_attempt_at, mode)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    from_umo,
                    to_umo,
                    kind or "notify",
                    clean,
                    due,
                    "pending",
                    _now(),
                    0,
                    due,
                    use_mode,
                ),
            )
            self._conn.commit()
        return self._task_row(task_id), ""

    def _update_task(self, task_id: str, status: str, **fields: Any) -> None:
        allowed = {"sent_at", "done_at", "error", "attempts", "next_attempt_at", "mode"}
        sets = ["status=?"]
        values: list[Any] = [status]
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key}=?")
                values.append(value)
        values.append(task_id)
        with self._lock:
            self._conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", values)
            self._conn.commit()

    async def _persona_prompt(self) -> str:
        try:
            config = self.context.get_config()
            pid = (
                config.get("agent_runner", {})
                .get("config", {})
                .get("persona", {})
                .get("persona_id", "default")
            )
            db = self.context.get_db()
            persona = await db.get_persona_by_id(pid)
            return str(getattr(persona, "system_prompt", "") or "")
        except Exception:
            logger.exception("[couple_ai] 读取人格失败")
            return ""

    async def _generate_text(self, umo: str, prompt: str, system_prompt: str = "") -> str:
        try:
            provider = self.context.get_using_provider(umo)
            if provider is None:
                return ""
            response = await provider.text_chat(
                prompt=prompt,
                system_prompt=system_prompt or None,
                contexts=[],
            )
            return str(getattr(response, "completion_text", "") or "").strip()
        except Exception as exc:
            logger.warning("[couple_ai] 目标语气生成失败: %s", exc)
            return ""

    async def _target_voice(self, task: dict) -> str:
        persona = await self._persona_prompt()
        system = (
            persona
            + "\n\n【跨房间传话】你收到一条来自 "
            + self._label(task["from_umo"])
            + " 的传话/提醒。请用你自己的语气，把这件事自然地告诉当前会话里的人；"
            "不要提“任务/系统/插件/日志”，不要复述格式；只说这一件事。"
        )
        prompt = (
            f"传话类型：{task['kind']}\n"
            f"内容：{task['text']}\n"
            "请用符合你人格的口吻，直接对当前会话的人说这句话。"
        )
        return await self._generate_text(task["to_umo"], prompt, system)

    async def _deliver_task(self, task: dict) -> None:
        max_attempts = max(1, int(self._cfg("task_max_attempts", 3)))
        retry_seconds = max(30, int(self._cfg("task_retry_seconds", 60)))
        attempts = int(task.get("attempts") or 0) + 1
        self._update_task(task["id"], "dispatching", attempts=attempts)
        try:
            text = await self._target_voice(task)
            if not text:
                raise RuntimeError("目标语气生成失败（provider 无返回）")
            prefix = str(self._cfg("deliver_prefix", "") or "")
            chain = MessageChain().message(prefix + text)
            ok = await self.context.send_message(task["to_umo"], chain)
            if not ok:
                raise RuntimeError("平台未找到目标会话")
            self._update_task(
                task["id"],
                "done",
                done_at=_now(),
                sent_at=_now(),
                error=None,
            )
            self._add_event(task["to_umo"], "task", text)
            logger.info("[couple_ai] 任务 %s 已完成 %s -> %s", task["id"], task["from_umo"], task["to_umo"])
        except Exception as exc:
            error = str(exc)[:180]
            if attempts >= max_attempts:
                self._update_task(task["id"], "failed", error=error)
                logger.error("[couple_ai] 任务 %s 失败（%s/%s）：%s", task["id"], attempts, max_attempts, error)
            else:
                self._update_task(
                    task["id"],
                    "pending",
                    error=error,
                    next_attempt_at=_now() + retry_seconds,
                )
                logger.warning("[couple_ai] 任务 %s 失败，%ss 后重试：%s", task["id"], retry_seconds, error)

    async def _dispatch_due(self) -> None:
        if not self.enabled:
            return
        now = _now()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status='pending' AND due_at <= ? "
                "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
                "ORDER BY due_at ASC LIMIT 3",
                (now, now),
            ).fetchall()
        for row in rows:
            task = dict(row)
            if not self._allow(task["to_umo"]):
                self._update_task(task["id"], "failed", error="目标会话不在白名单")
                continue
            await self._deliver_task(task)

    async def _maybe_diary(self) -> None:
        if not self._cfg("diary_enabled", False):
            return
        target = str(self._cfg("diary_target", "") or "").strip()
        if not target:
            return
        now = datetime.now()
        raw_time = str(self._cfg("diary_time", "23:00") or "23:00").strip()
        match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", raw_time)
        if not match:
            return
        if now.hour * 60 + now.minute < int(match.group(1)) * 60 + int(match.group(2)):
            return
        date = now.strftime("%Y-%m-%d")
        if self._meta_get("diary_last_date", "") == date:
            return
        try:
            content = await self._generate_diary(date)
            if not content:
                return
            self._meta_set("diary_last_date", date)
            await self._send_diary(date, content, target)
        except Exception:
            logger.exception("[couple_ai] 日记生成/发送失败")

    async def _generate_diary(self, date: str) -> str:
        start = _now() - 86400
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, umo, kind, text FROM events WHERE ts >= ? ORDER BY id ASC LIMIT 200",
                (start,),
            ).fetchall()
        lines = []
        for row in rows:
            stamp = datetime.fromtimestamp(row["ts"]).strftime("%H:%M")
            lines.append(f"- {stamp} {self._label(row['umo'])}：{row['text']}")
        persona = await self._persona_prompt()
        system = (
            persona
            + "\n\n【今日日记】请写一篇 Markdown 日记，第一人称、符合当前人格的口吻。"
            "重点记录你关心的两个人今天发生的事、你说过/听到的话、你的观察和感受；"
            "素材只来自下面的事件，不要编造；没有素材就写短一点。"
        )
        prompt = (
            f"日期：{date}\n"
            "今天的事件：\n"
            + ("\n".join(lines) if lines else "- 今天没有记录到事件")
            + "\n\n请输出完整 Markdown：标题、日期、分节（今天发生了什么 / 我想说的 / 关于他们俩）。"
        )
        content = await self._generate_text(self._cfg("diary_target", ""), prompt, system)
        if not content:
            return ""
        path = self.data_dir / "diaries" / f"{date}.md"
        path.write_text(content, encoding="utf-8")
        with self._lock:
            self._conn.execute(
                "INSERT INTO diaries(date,created_at,path,content,sent_to) VALUES(?,?,?,?,?) "
                "ON CONFLICT(date) DO UPDATE SET created_at=excluded.created_at,path=excluded.path,"
                "content=excluded.content,sent_to=excluded.sent_to",
                (date, _now(), str(path), content, self._cfg("diary_target", "")),
            )
            self._conn.commit()
        return content

    async def _send_diary(self, date: str, content: str, target: str) -> None:
        path = self.data_dir / "diaries" / f"{date}.md"
        sent = False
        try:
            chain = MessageChain([File(name=f"daily-diary-{date}.md", file=str(path))])
            sent = await self.context.send_message(target, chain)
        except Exception as exc:
            logger.warning("[couple_ai] 日记文件发送失败，回退文本: %s", exc)
        if not sent:
            limit = max(500, int(self._cfg("diary_max_chars", 4000)))
            sent = await self.context.send_message(target, MessageChain().message(content[:limit]))
        if sent:
            self._add_event(target, "diary", f"写了 {date} 的日记")
            logger.info("[couple_ai] 日记 %s 已发送 -> %s", date, target)

    async def _task_loop(self) -> None:
        interval = max(3, int(self._cfg("check_interval_seconds", 10)))
        while not self._stop:
            try:
                await self._dispatch_due()
                await self._maybe_diary()
            except Exception:
                logger.exception("[couple_ai] 任务循环异常")
            await asyncio.sleep(interval)

    # ── 生命周期 ─────────────────────────────────────────────────────────
    async def initialize(self) -> None:
        self._stop = False
        # 崩溃/重载时可能留下 dispatching，先回到 pending，避免任务永远卡住
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status='pending', next_attempt_at=? WHERE status='dispatching'",
                (_now(),),
            )
            self._conn.commit()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._task_loop())
        if self._cfg("disable_external_relay_tools", False):
            for name in (
                "pc_send_to_group",
                "pc_send_to_groups",
                "pc_send_to_private_user",
                "pc_send_to_private_users",
                "pc_relay_message",
                "pc_schedule_group_relay",
            ):
                try:
                    removed = self.context.deactivate_llm_tool(name)
                    if removed:
                        logger.info("[couple_ai] 已停用外部跨房间工具 %s", name)
                except Exception:
                    logger.exception("[couple_ai] 停用外部工具失败 %s", name)
        logger.info("[couple_ai] 跨房间任务、事件水印与日记已启动")

    async def terminate(self) -> None:
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
            self._task = None
        with self._lock:
            self._conn.close()
        logger.info("[couple_ai] 已停止")

    # ── LLM 工具 ─────────────────────────────────────────────────────────
    @filter.llm_tool(name="couple_task_create")
    async def couple_task_create(
        self,
        event: AstrMessageEvent,
        to_session: str,
        text: str,
        due_at: str = "",
        kind: str = "notify",
    ) -> str:
        """创建一个跨会话任务，把话/提醒交给另一个会话。

        Args:
            to_session(string): 目标会话 UMO，例如 default:FriendMessage:<目标QQ>
            text(string): 要传达的内容，200 字以内；目标会用自己的语气说
            due_at(string): ISO 8601 时间；留空表示立即
            kind(string): notify 或 remind
        """
        task, error = self._create_task(
            event.unified_msg_origin, to_session, text, due_at, kind
        )
        if error:
            return json.dumps({"ok": False, "error": error}, ensure_ascii=False)
        return json.dumps(
            {
                "ok": True,
                "id": task["id"],
                "status": task["status"],
                "to": task["to_umo"],
                "due_at": datetime.fromtimestamp(task["due_at"]).isoformat(),
            },
            ensure_ascii=False,
        )

    @filter.llm_tool(name="couple_task_list")
    async def couple_task_list(self, event: AstrMessageEvent, limit: int = 10) -> str:
        """查看当前会话相关的跨会话任务。

        Args:
            limit(number): 最多返回多少条，默认 10
        """
        umo = event.unified_msg_origin
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM tasks WHERE from_umo=? OR to_umo=?
                   ORDER BY created_at DESC LIMIT ?""",
                (umo, umo, max(1, min(50, int(limit)))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["from_label"] = self._label(item["from_umo"])
            item["to_label"] = self._label(item["to_umo"])
            result.append(item)
        return json.dumps({"ok": True, "tasks": result}, ensure_ascii=False)

    @filter.llm_tool(name="couple_task_cancel")
    async def couple_task_cancel(self, event: AstrMessageEvent, task_id: str) -> str:
        """取消一条还没发送的跨会话任务。

        Args:
            task_id(string): 任务 id
        """
        task = self._task_row(task_id)
        if not task:
            return json.dumps({"ok": False, "error": "任务不存在"}, ensure_ascii=False)
        if task["from_umo"] != event.unified_msg_origin and task["to_umo"] != event.unified_msg_origin:
            return json.dumps({"ok": False, "error": "只能取消自己相关任务"}, ensure_ascii=False)
        if task["status"] not in ("pending", "dispatching"):
            return json.dumps({"ok": False, "error": f"任务状态是 {task['status']}，不能取消"}, ensure_ascii=False)
        self._update_task(task_id, "cancelled")
        return json.dumps({"ok": True, "id": task_id, "status": "cancelled"}, ensure_ascii=False)

    @filter.llm_tool(name="couple_task_ack")
    async def couple_task_ack(self, event: AstrMessageEvent, task_id: str) -> str:
        """确认一条发给自己的跨房间任务已处理。

        Args:
            task_id(string): 任务 id
        """
        task = self._task_row(task_id)
        if not task:
            return json.dumps({"ok": False, "error": "任务不存在"}, ensure_ascii=False)
        if task["to_umo"] != event.unified_msg_origin and task["from_umo"] != event.unified_msg_origin:
            return json.dumps({"ok": False, "error": "只能确认自己相关任务"}, ensure_ascii=False)
        if task["status"] == "done":
            return json.dumps({"ok": True, "id": task_id, "already": True}, ensure_ascii=False)
        self._update_task(task_id, "done", done_at=_now())
        return json.dumps({"ok": True, "id": task_id, "status": "done"}, ensure_ascii=False)

    # ── 钩子 ─────────────────────────────────────────────────────────────
    @filter.on_llm_request()
    async def _inject_events(self, event: AstrMessageEvent, req: Any) -> None:
        if not self.enabled or not self._cfg("event_log_enabled", True):
            return
        block, max_id = self._events_block(event.unified_msg_origin)
        if not block:
            return

        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            # AstrBot < 4.24 没有这个能力。不要回退到修改 system_prompt，
            # 否则会破坏提示词缓存；直接跳过，等兼容版本再注入。
            logger.warning(
                "[couple_ai] 当前 AstrBot 不支持 extra_user_content_parts，跳过事件注入"
            )
            return

        try:
            part = TextPart(text=block)
            # 当前 API 是 mark_as_temp()；兼容审核说明里的 make_as_temp()。
            marker = getattr(part, "mark_as_temp", None) or getattr(
                part, "make_as_temp", None
            )
            if callable(marker):
                part = marker() or part
            parts.append(part)
        except Exception:
            logger.exception("[couple_ai] 注入事件上下文失败")
            return

        if max_id:
            self._set_watermark(event.unified_msg_origin, max_id)

    @filter.on_decorating_result()
    async def _log_outgoing(self, event: AstrMessageEvent) -> None:
        if not self.enabled or not self._cfg("event_log_enabled", True):
            return
        try:
            result = event.get_result()
            chain = getattr(result, "chain", None) or []
            text = "".join(
                str(getattr(comp, "text", "") or "")
                for comp in chain
                if isinstance(comp, Plain)
            ).strip()
            if text:
                self._add_event(event.unified_msg_origin, "say", text)
        except Exception:
            logger.exception("[couple_ai] 记录 outgoing 事件失败")

    # ── WebUI API ────────────────────────────────────────────────────────
    def _register_web_apis(self) -> None:
        routes = [
            ("config", self._api_config_get, ["GET"], "插件设置"),
            ("config/save", self._api_config_save, ["POST"], "保存插件设置"),
            ("overview", self._api_overview, ["GET"], "任务、事件与日记概览"),
            ("task/create", self._api_task_create, ["POST"], "创建任务"),
            ("task/cancel", self._api_task_cancel, ["POST"], "取消任务"),
            ("task/retry", self._api_task_retry, ["POST"], "重试任务"),
            ("task/done", self._api_task_done, ["POST"], "标记完成"),
            ("event/clear", self._api_event_clear, ["POST"], "清空事件"),
            ("diary/list", self._api_diary_list, ["GET"], "日记列表"),
            ("diary/read", self._api_diary_read, ["GET"], "读取日记"),
            ("diary/generate", self._api_diary_generate, ["POST"], "生成日记"),
            ("diary/delete", self._api_diary_delete, ["POST"], "删除日记"),
        ]
        # 同时注册 metadata name 和目录名：不同 AstrBot 版本的插件页上下文
        # 可能用 couple_ai，也可能用 astrbot_plugin_couple_ai。
        for prefix in dict.fromkeys((PLUGIN_NAME, "astrbot_plugin_couple_ai")):
            for route, handler, methods, desc in routes:
                self.context.register_web_api(f"/{prefix}/{route}", handler, methods, desc)

    @staticmethod
    def _umo_session_id(umo: str) -> str:
        parts = str(umo or "").split(":")
        return parts[-1] if len(parts) >= 3 else ""

    def _config_view(self) -> dict:
        labels = self._labels()
        allowed = [x.strip() for x in str(self._cfg("allow_sessions", "") or "").splitlines() if x.strip()]
        umos = list(dict.fromkeys(list(labels.keys()) + allowed))
        friend_umos = [u for u in umos if ":FriendMessage:" in u]
        group_umos = [u for u in umos if ":GroupMessage:" in u]
        diary_target = str(self._cfg("diary_target", "") or "").strip()
        owner_umo = diary_target if ":FriendMessage:" in diary_target else (friend_umos[0] if friend_umos else "")
        partner_umo = next((u for u in friend_umos if u != owner_umo), "")
        group_umo = group_umos[0] if group_umos else ""
        platform = (
            (owner_umo or partner_umo or group_umo or "").split(":")[0]
            or str(self._cfg("platform_id", "default") or "default")
        )
        return {
            "enabled": bool(self._cfg("enabled", True)),
            "event_log_enabled": bool(self._cfg("event_log_enabled", True)),
            "event_inject_count": int(self._cfg("event_inject_count", 5)),
            "event_max_chars": int(self._cfg("event_max_chars", 80)),
            "event_keep": int(self._cfg("event_keep", 200)),
            "task_max_pending": int(self._cfg("task_max_pending", 20)),
            "task_max_chars": int(self._cfg("task_max_chars", 200)),
            "check_interval_seconds": int(self._cfg("check_interval_seconds", 10)),
            "disable_external_relay_tools": bool(self._cfg("disable_external_relay_tools", False)),
            "diary_enabled": bool(self._cfg("diary_enabled", False)),
            "diary_time": str(self._cfg("diary_time", "23:00") or "23:00"),
            "diary_target": diary_target or owner_umo,
            "deliver_prefix": str(self._cfg("deliver_prefix", "") or ""),
            "platform_id": platform,
            "owner_qq": str(self._cfg("owner_qq", "") or self._umo_session_id(owner_umo)),
            "partner_qq": str(self._cfg("partner_qq", "") or self._umo_session_id(partner_umo)),
            "group_id": str(self._cfg("group_id", "") or self._umo_session_id(group_umo)),
            "owner_name": str(self._cfg("owner_name", "") or labels.get(owner_umo, "我")),
            "partner_name": str(self._cfg("partner_name", "") or labels.get(partner_umo, "对方")),
            "group_name": str(self._cfg("group_name", "") or labels.get(group_umo, "小群")),
            "restrict_sessions": bool(allowed),
        }

    async def _api_config_get(self):
        return jsonify({"ok": True, "config": self._config_view()})

    async def _api_config_save(self):
        body = await request.get_json(silent=True) or {}

        def text(key: str, default: str = "") -> str:
            value = body.get(key, default)
            return str(value if value is not None else default).strip()

        def flag(key: str, default: bool = False) -> bool:
            value = body.get(key, default)
            return bool(value)

        def number(key: str, default: int, low: int, high: int) -> int:
            try:
                return max(low, min(high, int(body.get(key, default))))
            except (TypeError, ValueError):
                return default

        platform = text("platform_id", "default") or "default"
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", platform):
            return jsonify({"ok": False, "error": "平台 ID 只能包含字母、数字、下划线、点和短横线"}), 400
        owner_qq = text("owner_qq")
        partner_qq = text("partner_qq")
        group_id = text("group_id")
        for label, value in (("我的 QQ", owner_qq), ("对方 QQ", partner_qq), ("小群号", group_id)):
            if value and not value.isdigit():
                return jsonify({"ok": False, "error": f"{label} 只能是数字"}), 400
        owner_umo = f"{platform}:FriendMessage:{owner_qq}" if owner_qq else ""
        partner_umo = f"{platform}:FriendMessage:{partner_qq}" if partner_qq else ""
        group_umo = f"{platform}:GroupMessage:{group_id}" if group_id else ""
        names = {
            owner_umo: text("owner_name", "我") or "我",
            partner_umo: text("partner_name", "对方") or "对方",
            group_umo: text("group_name", "小群") or "小群",
        }
        labels = "\n".join(f"{umo}={name}" for umo, name in names.items() if umo)
        restrict = flag("restrict_sessions", True)
        allow = "\n".join(u for u in (owner_umo, partner_umo, group_umo) if u) if restrict else ""
        diary_target = text("diary_target") or owner_umo
        if diary_target and ":" not in diary_target:
            return jsonify({"ok": False, "error": "日记目标必须是 UMO，例如 default:FriendMessage:<QQ号>"}), 400
        updates = {
            "enabled": flag("enabled", True),
            "event_log_enabled": flag("event_log_enabled", True),
            "event_inject_count": number("event_inject_count", 5, 0, 10),
            "event_max_chars": number("event_max_chars", 80, 20, 200),
            "event_keep": number("event_keep", 200, 20, 500),
            "task_max_pending": number("task_max_pending", 20, 1, 100),
            "task_max_chars": number("task_max_chars", 200, 20, 500),
            "check_interval_seconds": number("check_interval_seconds", 10, 3, 300),
            "disable_external_relay_tools": flag("disable_external_relay_tools", False),
            "diary_enabled": flag("diary_enabled", False),
            "diary_time": text("diary_time", "23:00") or "23:00",
            "diary_target": diary_target,
            "deliver_prefix": text("deliver_prefix"),
            "platform_id": platform,
            "owner_qq": owner_qq,
            "partner_qq": partner_qq,
            "group_id": group_id,
            "owner_name": text("owner_name", "我") or "我",
            "partner_name": text("partner_name", "对方") or "对方",
            "group_name": text("group_name", "小群") or "小群",
            "restrict_sessions": restrict,
            "session_labels": labels,
            "allow_sessions": allow,
        }
        try:
            self.config.update(updates)
            self.config.save_config()
        except Exception as exc:
            logger.exception("[couple_ai] 保存配置失败")
            return jsonify({"ok": False, "error": f"保存失败：{exc}"}), 500
        return jsonify({"ok": True, "config": self._config_view()})

    async def _api_overview(self):
        with self._lock:
            task_rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
            event_rows = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT 60"
            ).fetchall()
            diary_rows = self._conn.execute(
                "SELECT date, created_at, sent_to FROM diaries ORDER BY date DESC LIMIT 20"
            ).fetchall()
            counts = {
                "pending": self._conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status IN ('pending','dispatching')"
                ).fetchone()[0],
                "sent": self._conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status='done'"
                ).fetchone()[0],
                "done": self._conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status='done'"
                ).fetchone()[0],
                "events": self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                "diaries": self._conn.execute("SELECT COUNT(*) FROM diaries").fetchone()[0],
            }
        tasks = []
        for row in task_rows:
            item = dict(row)
            item["from_label"] = self._label(item["from_umo"])
            item["to_label"] = self._label(item["to_umo"])
            item["due_text"] = datetime.fromtimestamp(item["due_at"]).strftime("%m-%d %H:%M")
            tasks.append(item)
        events = []
        for row in reversed(event_rows):
            item = dict(row)
            item["label"] = self._label(item["umo"])
            item["time_text"] = datetime.fromtimestamp(row["ts"]).strftime("%m-%d %H:%M")
            events.append(item)
        diaries = []
        for row in diary_rows:
            item = dict(row)
            item["created_text"] = datetime.fromtimestamp(row["created_at"]).strftime("%m-%d %H:%M")
            diaries.append(item)
        return jsonify(
            {"ok": True, "tasks": tasks, "events": events, "diaries": diaries, "counts": counts}
        )

    async def _api_task_create(self):
        body = await request.get_json(silent=True) or {}
        from_umo = str(body.get("from_umo") or "webui").strip()
        task, error = self._create_task(
            from_umo,
            str(body.get("to_session") or "").strip(),
            str(body.get("text") or ""),
            body.get("due_at"),
            str(body.get("kind") or "notify"),
        )
        if error:
            return jsonify({"ok": False, "error": error}), 400
        return jsonify({"ok": True, "task": task})

    async def _api_task_cancel(self):
        body = await request.get_json(silent=True) or {}
        task = self._task_row(str(body.get("id") or ""))
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        if task["status"] not in ("pending", "dispatching"):
            return jsonify({"ok": False, "error": f"状态 {task['status']} 不能取消"}), 400
        self._update_task(task["id"], "cancelled")
        return jsonify({"ok": True})

    async def _api_task_retry(self):
        body = await request.get_json(silent=True) or {}
        task = self._task_row(str(body.get("id") or ""))
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status='pending', attempts=0, next_attempt_at=?, due_at=?, "
                "error=NULL, sent_at=NULL, done_at=NULL WHERE id=?",
                (_now(), _now(), task["id"]),
            )
            self._conn.commit()
        return jsonify({"ok": True})

    async def _api_task_done(self):
        body = await request.get_json(silent=True) or {}
        task = self._task_row(str(body.get("id") or ""))
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        self._update_task(task["id"], "done", done_at=_now())
        return jsonify({"ok": True})

    async def _api_event_clear(self):
        with self._lock:
            self._conn.execute("DELETE FROM events")
            self._conn.execute("DELETE FROM event_seen")
            self._conn.commit()
        return jsonify({"ok": True})

    async def _api_diary_list(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT date, created_at, sent_to, length(content) AS size FROM diaries ORDER BY date DESC"
            ).fetchall()
        return jsonify({"ok": True, "diaries": [dict(r) for r in rows]})

    async def _api_diary_read(self):
        date = str(request.args.get("date") or "").strip()
        with self._lock:
            row = self._conn.execute(
                "SELECT date, created_at, content FROM diaries WHERE date=?", (date,)
            ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "日记不存在"}), 404
        return jsonify({"ok": True, "diary": dict(row)})

    async def _api_diary_generate(self):
        body = await request.get_json(silent=True) or {}
        date = str(body.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
        send = bool(body.get("send", True))
        content = await self._generate_diary(date)
        if not content:
            return jsonify({"ok": False, "error": "生成失败（模型不可用或返回为空）"}), 500
        if send:
            target = str(self._cfg("diary_target", "") or "").strip()
            if target:
                await self._send_diary(date, content, target)
        return jsonify({"ok": True, "date": date, "content": content})

    async def _api_diary_delete(self):
        body = await request.get_json(silent=True) or {}
        date = str(body.get("date") or "").strip()
        with self._lock:
            self._conn.execute("DELETE FROM diaries WHERE date=?", (date,))
            self._conn.commit()
        path = self.data_dir / "diaries" / f"{date}.md"
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return jsonify({"ok": True})
