"""调用本机 Antigravity（agy CLI）headless 模式执行任务，把结果回传飞书。

`agy -p <prompt> --output-format json` 以非交互方式处理消息，并输出结果 JSON
（含 status / conversation_id / response / duration_seconds / usage 等）。

会话续聊：首轮或重置后无需传 `--conversation`（agy 自动生成新 conversation_id 并返回），
后续多轮消息通过 `--conversation <id>` 延续上下文；桥接层把 conversation_id 记录在
.feishu_sessions.json 中，由所有飞书消息共用。

权限模式：默认 `bypassPermissions`（映射为 `--dangerously-skip-permissions`，
无人值守自动放行工具执行）。
"""

import json
import logging
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

LOGGER = logging.getLogger("feishu_codex_bridge.agy")


def _build_cmd(
    agy_bin: str,
    prompt: str,
    session_id: Optional[str] = None,
    *,
    permission_mode: Optional[str] = "bypassPermissions",
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> list:
    """组装 agy 命令行；-p 后直接跟 prompt。"""
    cmd = [agy_bin, "--output-format", "json"]
    if session_id:
        cmd += ["--conversation", session_id]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if permission_mode == "bypassPermissions":
        cmd.append("--dangerously-skip-permissions")
    elif permission_mode:
        cmd += ["--mode", permission_mode]
    cmd += ["-p", prompt]
    return cmd


def _run_once(
    cmd: list, workdir: str, env: dict, timeout: float
) -> tuple[int, str, str, bool]:
    """跑一次 agy，返回 (returncode, stdout, stderr, timed_out)。"""
    proc = subprocess.Popen(
        cmd,
        cwd=workdir,
        env=env,
        stdin=subprocess.DEVNULL,
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
    """从 stdout 解析 agy 的结果 JSON（整段或最后一行非空行）。"""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict) and ("response" in data or "status" in data):
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
        if isinstance(data, dict) and ("response" in data or "status" in data):
            return data
    return None


def _format_result(data: dict) -> str:
    """把结果 JSON 转成给飞书的文本。"""
    cid = data.get("conversation_id")
    status = data.get("status")
    duration = data.get("duration_seconds")
    turns = data.get("num_turns")
    usage = data.get("usage") or {}
    LOGGER.info(
        "Antigravity 完成: status=%s duration=%.2fs turns=%s tokens=%s conversation=%s",
        status,
        duration or 0.0,
        turns,
        usage.get("total_tokens"),
        cid,
    )
    if status != "SUCCESS":
        err_text = str(data.get("error") or data.get("response") or "未知错误").strip()
        return f"❌ Antigravity 执行失败：\n{err_text}"
    text = str(data.get("response") or "").strip()
    return text or "✅ 处理完成。"


def run_agy(
    prompt: str,
    agy_bin: str,
    workdir: str,
    timeout: int,
    on_progress: Optional[Callable[[str], None]] = None,
    progress_interval: float = 10.0,
    session_id: Optional[str] = None,
    permission_mode: Optional[str] = "bypassPermissions",
    model: Optional[str] = None,
    effort: Optional[str] = None,
    extra_env: Optional[dict] = None,
    on_session_id: Optional[Callable[[str], None]] = None,
    **kwargs,
) -> str:
    """运行 agy headless 并返回最终答复文本。

    session_id 非空时传入 `--conversation <session_id>` 续聊；为空时不传，
    agy 会自动创建新会话并在输出 JSON 中返回 conversation_id。
    当收到新的 conversation_id 时，通过 on_session_id 回调持久化。
    """
    cmd = _build_cmd(
        agy_bin,
        prompt,
        session_id=session_id,
        permission_mode=permission_mode,
        model=model,
        effort=effort,
    )
    LOGGER.info(
        "调用 Antigravity: %s (session=%s, model=%s, effort=%s)",
        prompt[:200],
        session_id or "(新建)",
        model or "(默认)",
        effort or "(默认)",
    )

    env = dict(os.environ)
    if isinstance(extra_env, dict) and extra_env:
        env.update(extra_env)

    ret, out, err, timed_out = _run_once(cmd, workdir, env, float(timeout))

    if timed_out:
        LOGGER.warning("Antigravity 任务超时（%s 秒）", timeout)
        return f"⏱️ Antigravity 执行超时（超过 {timeout} 秒），已终止任务。"

    data = _parse_result(out)
    if data is None:
        if ret != 0:
            err_msg = (err or out).strip()
            LOGGER.error("Antigravity 进程异常退出: ret=%s err=%s", ret, err_msg)
            return f"❌ Antigravity 执行出错（退出码 {ret}）：\n{err_msg[:1000]}"
        text = (out or "").strip()
        return text or "✅ 处理完成。"

    new_cid = data.get("conversation_id")
    if new_cid and on_session_id:
        try:
            on_session_id(str(new_cid))
        except Exception:
            LOGGER.exception("保存 Antigravity 会话 ID 失败")

    return _format_result(data)
