"""调用本机 Claude Code（claude CLI）headless 模式执行任务，把结果回传飞书。

`claude -p <prompt> --output-format json` 以非交互方式处理一条消息，并打印
最终答复的 JSON（含 result / session_id / is_error / total_cost_usd）。

会话续聊：用 `--session-id <uuid>` 创建 / `--resume <uuid>` 复用持久化会话
（存 ~/.claude/projects/<工作目录转义>/<uuid>.jsonl），桥接层所有飞书消息
共用一个固定会话 id（UUID 格式）。

认证：claude 需要 ANTHROPIC_AUTH_TOKEN（本机配在 ~/.zshrc，launchd 启动的
桥接进程拿不到），由桥接层通过 config 的 claude_env 注入子进程环境。
"""

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

LOGGER = logging.getLogger("feishu_codex_bridge.claude")

# claude CLI 的会话不存在 / 会话 id 被占用 的错误串（用于自动回退重试）。
_NO_CONVERSATION_RE = re.compile(r"No conversation found with session ID", re.IGNORECASE)
_ALREADY_IN_USE_RE = re.compile(r"is already in use", re.IGNORECASE)


def _claude_projects_dir(workdir: str) -> Path:
    """claude 会话文件根目录：~/.claude/projects/<转义后的工作目录>/。

    转义规则：每个非 [A-Za-z0-9] 字符替换为一个 ``-``（不折叠）。
    """
    escaped = re.sub(r"[^A-Za-z0-9]", "-", workdir)
    return Path.home() / ".claude" / "projects" / escaped


def session_exists(
    session_id: str, workdir: str, projects_dir: Optional[Path] = None
) -> bool:
    """会话文件是否存在（仅作快路径优化，正确性靠运行时双向回退兜底）。

    任何异常/不确定都返回 False——最多多试一次 create，不会更糟。
    """
    try:
        root = projects_dir if projects_dir is not None else _claude_projects_dir(workdir)
        return (root / f"{session_id}.jsonl").is_file()
    except OSError:
        return False


def _build_cmd(
    claude_bin: str,
    prompt: str,
    session_id: Optional[str],
    *,
    resume: bool,
    permission_mode: Optional[str],
    model: Optional[str] = None,
) -> list:
    """组装 claude 命令行；prompt 放在末尾的 ``--`` 之后。

    ``--`` 终止选项解析：以 ``-`` 开头的指令（如 "-42 是什么"）不会被
    误当成选项（实测 ``claude -p ... -- -42 xx`` 正常）。
    """
    cmd = [claude_bin, "-p", "--output-format", "json"]
    if session_id:
        cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
    if model:
        cmd += ["--model", model]
    if permission_mode == "bypassPermissions":
        cmd.append("--dangerously-skip-permissions")
    elif permission_mode:
        cmd += ["--permission-mode", permission_mode]
    cmd += ["--", prompt]
    return cmd


def _run_once(
    cmd: list, workdir: str, env: dict, timeout: float
) -> tuple:
    """跑一次 claude，返回 (returncode, stdout, stderr, timed_out)。"""
    proc = subprocess.Popen(
        cmd,
        cwd=workdir,
        env=env,
        stdin=subprocess.DEVNULL,  # 防止 claude 在管道环境下把 stdin 并入 prompt
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    state = {"timed_out": False}

    def _kill() -> None:
        if proc.poll() is None:
            state["timed_out"] = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()

    timer = threading.Timer(timeout, _kill)
    timer.daemon = True
    timer.start()
    try:
        out, err = proc.communicate()
    finally:
        timer.cancel()
    return proc.returncode, out, err, state["timed_out"]


def _parse_result(stdout: str) -> Optional[dict]:
    """从 stdout 解析 claude 的结果 JSON（整段或最后一行非空行）。"""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "result" in data:
            return data
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and ("result" in data or data.get("type") == "result"):
            return data
    return None


def _format_result(data: dict) -> str:
    """把结果 JSON 转成给飞书的文本。"""
    sid = data.get("session_id")
    LOGGER.info(
        "Claude 完成: duration=%sms turns=%s cost=$%s session=%s",
        data.get("duration_ms"),
        data.get("num_turns"),
        data.get("total_cost_usd"),
        sid,
    )
    if data.get("is_error"):
        subtype = data.get("subtype") or "error"
        text = str(data.get("result") or "").strip()
        if text:
            return f"❌ Claude 执行失败（{subtype}）：\n{text}"
        return f"❌ Claude 执行失败（{subtype}）。"
    text = str(data.get("result") or "").strip()
    return text or "✅ 处理完成。"


def _notify_progress(on_progress: Optional[Callable[[str], None]], text: str) -> None:
    if not on_progress:
        return
    try:
        on_progress(text)
    except Exception:
        LOGGER.exception("发送过程提示失败")


def run_claude(
    prompt: str,
    claude_bin: str,
    workdir: str,
    timeout: int,
    on_progress: Optional[Callable[[str], None]] = None,
    progress_interval: float = 10.0,
    session_id: Optional[str] = None,
    permission_mode: Optional[str] = "bypassPermissions",
    extra_env: Optional[dict] = None,
    model: Optional[str] = None,
) -> str:
    """运行 claude headless 并返回最终答复文本。

    session_id 非空时：会话文件不存在 → 直接 --session-id 创建；存在 →
    --resume 续聊。两种方式失败且错误串匹配会话状态问题时自动回退一次
    （resume 报 "No conversation found" → 改 create；create 报
    "already in use" → 改 resume）。超时和其它错误一律不回退。

    permission_mode 为 bypassPermissions（默认）时传
    --dangerously-skip-permissions，其它非空值传 --permission-mode <值>。
    extra_env 非空时叠加进子进程环境（用于注入 ANTHROPIC_AUTH_TOKEN）。
    model 非空时传 --model（默认留空 = 用 claude CLI 的当前默认模型）。

    progress_interval 保留参数以兼容 run_dsh 的调用签名（claude JSON 模式
    无事件流，目前只在开始时发一次过程提示）。
    """
    env = dict(os.environ)
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    if not (env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")):
        LOGGER.error(
            "未检测到 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY，"
            "claude 调用可能认证失败（launchd 环境需在 config.json 的 claude_env 中注入）"
        )

    deadline = time.monotonic() + timeout
    if session_id is None:
        attempts = [False]  # 无会话模式：与 dsh 的「一次性任务」语义一致
    elif session_exists(session_id, workdir):
        attempts = [True, False]  # 先 resume，失败回退 create
    else:
        attempts = [False, True]  # 先 create（新会话/已重置），撞占用再 resume

    for idx, resume in enumerate(attempts):
        mode = "resume" if resume else "create"
        cmd = _build_cmd(
            claude_bin,
            prompt,
            session_id,
            resume=resume,
            permission_mode=permission_mode,
            model=model,
        )
        LOGGER.info(
            "调用 Claude Code: %s (session=%s, mode=%s)",
            prompt[:200],
            session_id or "-",
            mode,
        )
        budget = max(5.0, deadline - time.monotonic())
        if idx == 0:
            _notify_progress(on_progress, "🧠 Claude 正在处理…")
        rc, out, err, timed_out = _run_once(cmd, workdir, env, budget)

        if timed_out:
            # 超时说明任务可能仍在工作，回退换会话等于丢上下文，绝不回退。
            return f"⚠️ Claude 任务超过 {timeout} 秒未完成，已终止。可以换更简单的指令重试。"

        if rc == 0:
            data = _parse_result(out)
            if data is not None:
                return _format_result(data)
            # 非 JSON 输出（理论上不会发生）：原样透传
            return out.strip() or err.strip() or "✅ 处理完成。"

        has_next = idx + 1 < len(attempts)
        if has_next and _should_flip(err, tried_resume=resume):
            LOGGER.info(
                "会话 %s 的 %s 失败（会话状态不匹配），自动改用 %s 重试",
                session_id,
                mode,
                "create" if resume else "resume",
            )
            continue

        detail = (err or "").strip() or (out or "").strip()
        if detail:
            return f"❌ Claude 执行失败（退出码 {rc}）：\n{detail[-1500:]}"
        return f"❌ Claude 执行失败（退出码 {rc}），无输出。"

    return "❌ Claude 执行失败：未知错误。"  # 理论不可达（循环内必 return）


def _should_flip(err: str, *, tried_resume: bool) -> bool:
    """失败是否属于「换另一种会话方式可解决」的情况。"""
    if tried_resume:
        return bool(_NO_CONVERSATION_RE.search(err or ""))
    return bool(_ALREADY_IN_USE_RE.search(err or ""))
