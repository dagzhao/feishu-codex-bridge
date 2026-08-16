"""读取本机 Codex 会话记录。"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger("feishu_codex_bridge.sessions")

DEFAULT_TITLE = "（无标题）"
_DESKTOP_TITLE_PREFIX = "🤖 "
_GUARDIAN_TITLE_PREFIX = "The following is the Codex agent history"


def sessions_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(codex_home) / "sessions"


def locks_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(codex_home) / "thread-writer-locks"


def index_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(codex_home) / "session_index.jsonl"


def register_in_desktop_index(session_id: str, title: str) -> bool:
    """把会话登记到桌面端会话列表，返回是否新增登记。"""
    path = index_path()
    if not path.exists():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if json.loads(line).get("id") == session_id:
                return False
    except (OSError, json.JSONDecodeError):
        pass
    entry = {
        "id": session_id,
        "thread_name": title[:50],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _session_files(session_id: str) -> list:
    root = sessions_dir()
    if not root.exists():
        return []
    return [p for p in root.rglob("*.jsonl") if _session_id(p) == session_id]


def mark_as_desktop(session_id: str) -> bool:
    """把会话标记为桌面端会话（source=vscode），使其出现在桌面端最近列表。"""
    changed = False
    for path in _session_files(session_id):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if not lines:
            continue
        try:
            meta = json.loads(lines[0])
        except json.JSONDecodeError:
            continue
        payload = meta.get("payload") or {}
        if payload.get("source") != "exec":
            continue
        payload["source"] = "vscode"
        meta["payload"] = payload
        lines[0] = json.dumps(meta, ensure_ascii=False)
        tmp = path.with_suffix(".jsonl.tmp")
        try:
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, path)
            changed = True
        except OSError:
            continue
    if changed:
        _update_threads_source(session_id, "vscode")
    _update_threads_title(session_id)
    _update_index_title(session_id)
    return changed


def _update_threads_source(session_id: str, source: str) -> None:
    db_path = index_path().parent / "state_5.sqlite"
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            conn.execute("UPDATE threads SET source=? WHERE id=?", (source, session_id))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        LOGGER.exception("更新桌面端线程表失败")


def _prefixed_title(title: str) -> str:
    return title if title.startswith(_DESKTOP_TITLE_PREFIX) else _DESKTOP_TITLE_PREFIX + title


def _update_threads_title(session_id: str) -> None:
    db_path = index_path().parent / "state_5.sqlite"
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            row = conn.execute("SELECT title FROM threads WHERE id=?", (session_id,)).fetchone()
            if row and row[0]:
                new_title = _prefixed_title(row[0])
                conn.execute(
                    "UPDATE threads SET title=?, name=?, preview=?, first_user_message=? WHERE id=?",
                    (new_title, new_title, new_title, new_title, session_id),
                )
                conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        LOGGER.exception("更新桌面端线程标题失败")


def _update_index_title(session_id: str) -> None:
    path = index_path()
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    changed = False
    for i, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("id") == session_id and entry.get("thread_name"):
            new_name = _prefixed_title(entry["thread_name"])
            if new_name != entry["thread_name"]:
                entry["thread_name"] = new_name
                lines[i] = json.dumps(entry, ensure_ascii=False)
                changed = True
    if changed:
        try:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass


def locked_session_ids() -> set:
    """返回当前正在被 Codex（桌面端等）占用的会话 ID 集合。"""
    root = locks_dir()
    if not root.exists():
        return set()
    locked = set()
    for path in root.glob("*.lock"):
        session_id = path.stem
        if session_id.startswith("."):
            continue
        locked.add(session_id)
    return locked


def _session_id(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "session_meta":
                    payload = record.get("payload") or {}
                    if payload.get("session_id"):
                        return payload["session_id"]
                break
    except OSError:
        pass
    stem = path.stem.removeprefix("rollout-")
    return "-".join(stem.split("-")[-5:])


def _read_meta(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "session_meta":
                    return record.get("payload") or {}
                break
    except OSError:
        pass
    return {}


def _extract_title(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = record.get("payload") or {}
                if record.get("type") == "event_msg" and payload.get("type") == "user_message":
                    text = (payload.get("message") or "").strip()
                    if text:
                        return text[:30]
    except OSError:
        pass
    return DEFAULT_TITLE


def _threads_db() -> Optional[Path]:
    path = index_path().parent / "state_5.sqlite"
    return path if path.exists() else None


def _list_from_threads(limit: Optional[int], skip_exec: bool) -> Optional[list]:
    """从桌面端会话表读取会话；表不可用时返回 None（由调用方走文件兜底）。"""
    db_path = _threads_db()
    if db_path is None:
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        query = (
            "SELECT id, title, first_user_message, updated_at, rollout_path "
            "FROM threads WHERE archived=0 AND source NOT LIKE '{%'"
        )
        params: list = []
        if skip_exec:
            query += " AND source != 'exec'"
        query += " ORDER BY updated_at DESC"
        rows = conn.execute(query, params).fetchall()
    except sqlite3.Error:
        LOGGER.exception("读取桌面端会话表失败")
        return None
    finally:
        conn.close()

    result = []
    for sid, title, first_msg, updated_at, rollout_path in rows:
        if title and title.startswith(_GUARDIAN_TITLE_PREFIX):
            continue
        if rollout_path and not os.path.exists(rollout_path):
            continue
        title = (title or "").strip() or (first_msg or "").strip() or DEFAULT_TITLE
        result.append({
            "id": sid,
            "title": title[:50],
            "time": datetime.fromtimestamp(updated_at).strftime("%m-%d %H:%M"),
        })
    return result


def _list_from_files(limit: Optional[int], skip_exec: bool) -> list:
    """兜底：从会话记录文件按更新时间列出。"""
    root = sessions_dir()
    if not root.exists():
        return []
    files = []
    for path in root.rglob("*.jsonl"):
        if skip_exec and _read_meta(path).get("source") == "exec":
            continue
        try:
            files.append((path.stat().st_mtime, path))
        except OSError:
            continue
    files.sort(reverse=True)
    if limit is not None:
        files = files[:limit]
    result = []
    seen = set()
    for mtime, path in files:
        session_id = _session_id(path)
        if session_id in seen:
            continue
        seen.add(session_id)
        result.append({
            "id": session_id,
            "title": _extract_title(path),
            "time": datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M"),
        })
    return result


def list_recent_sessions(limit: Optional[int] = None, skip_exec: bool = True) -> list:
    """列出本机 Codex 会话（按最近更新排序）。

    limit 为 None 时列出全部；skip_exec=True 时过滤掉 exec 一次性会话、
    子代理/审批类后台会话。
    """
    rows = _list_from_threads(limit, skip_exec)
    if rows is not None:
        if limit is not None:
            rows = rows[:limit]
        return rows
    return _list_from_files(limit, skip_exec)


def latest_session_id(exclude: Optional[set] = None, after: Optional[float] = None) -> Optional[str]:
    """返回最近创建（或更新）的会话 ID；after 指定后只统计该时间之后更新的文件。"""
    exclude = exclude or set()
    root = sessions_dir()
    if not root.exists():
        return None
    best = None
    for path in root.rglob("*.jsonl"):
        session_id = _session_id(path)
        if session_id in exclude:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if after is not None and mtime <= after:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, session_id)
    return best[1] if best else None
