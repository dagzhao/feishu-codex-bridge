"""后端注册表：按 config 的 backend 开关分派 dsh / claude 任务执行。

每个后端一条元数据（可执行文件配置键、权限配置键、会话 id 规则、运行函数），
bridge 层只依赖 ``get_backend`` / ``run_task``，不再直接 import 具体后端模块。
新增后端 = 加一条注册表条目。
"""

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from .claude import run_claude
from .dsh import run_dsh

LOGGER = logging.getLogger("feishu_codex_bridge.backend")

DEFAULT_BACKEND = "claude"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _new_claude_session_id() -> str:
    """claude 会话 id：必须是合法 UUID（--session-id 的要求）。"""
    return str(uuid.uuid4())


def _new_dsh_session_id() -> str:
    """dsh 会话 id：沿用历史格式 session-feishu-<hex8>。"""
    return f"session-feishu-{uuid.uuid4().hex[:8]}"


def _looks_like_uuid(sid: str) -> bool:
    return bool(_UUID_RE.match(str(sid)))


def _looks_like_dsh_session(sid: str) -> bool:
    return str(sid).startswith("session-feishu-")


@dataclass
class Backend:
    name: str
    label: str  # 用户可见文案/卡片标题用的显示名
    bin_key: str  # config 里可执行文件路径的键
    perm_key: str  # config 里权限模式的键
    default_perm: str
    new_session_id: Callable[[], str]
    looks_like: Callable[[str], bool]
    run: Callable[..., str]
    env_key: Optional[str] = None  # config 里额外注入子进程环境变量的键（可空）
    model_key: Optional[str] = None  # config 里模型名的键（可空；留空 = 用后端默认模型）


BACKENDS: dict[str, Backend] = {
    "claude": Backend(
        name="claude",
        label="Claude",
        bin_key="claude_bin",
        perm_key="claude_permission_mode",
        default_perm="bypassPermissions",
        new_session_id=_new_claude_session_id,
        looks_like=_looks_like_uuid,
        run=run_claude,
        env_key="claude_env",
        model_key="claude_model",
    ),
    "dsh": Backend(
        name="dsh",
        label="DSH",
        bin_key="dsh_bin",
        perm_key="dsh_permission_mode",
        default_perm="danger-full-access",
        new_session_id=_new_dsh_session_id,
        looks_like=_looks_like_dsh_session,
        run=run_dsh,
    ),
}


def get_backend(config: dict) -> Backend:
    """读取 config 的 backend 开关；未知值回落到默认后端（不退出）。"""
    name = (config or {}).get("backend") or DEFAULT_BACKEND
    backend = BACKENDS.get(name)
    if backend is None:
        LOGGER.warning("未知的 backend 配置 %r，回落到 %s", name, DEFAULT_BACKEND)
        return BACKENDS[DEFAULT_BACKEND]
    return backend


def run_task(
    backend: Backend,
    config: dict,
    prompt: str,
    *,
    workdir: str,
    timeout: int,
    on_progress: Optional[Callable[[str], None]] = None,
    progress_interval: float = 10.0,
    session_id: Optional[str] = None,
) -> str:
    """按后端元数据取配置并执行任务；可执行文件缺失时返回友好文案。"""
    bin_path = config.get(backend.bin_key) or backend.name
    kwargs = {}
    if backend.env_key:
        env = config.get(backend.env_key)
        if isinstance(env, dict) and env:
            kwargs["extra_env"] = env
    if backend.model_key:
        model = config.get(backend.model_key)
        if model:
            kwargs["model"] = model
    try:
        return backend.run(
            prompt,
            bin_path,
            workdir,
            timeout,
            on_progress=on_progress,
            progress_interval=progress_interval,
            session_id=session_id,
            permission_mode=config.get(backend.perm_key, backend.default_perm),
            **kwargs,
        )
    except (FileNotFoundError, PermissionError):
        LOGGER.exception("后端可执行文件不可用: %s", bin_path)
        return (
            f"❌ 找不到 {backend.label} 可执行文件（{bin_path}），"
            f"请检查 config.json 的 {backend.bin_key} 配置。"
        )
