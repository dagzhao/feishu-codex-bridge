"""配置加载与日志初始化。"""

import json
import logging
import os
import shutil
import sys
from pathlib import Path

from .backend import BACKENDS as _BACKENDS, DEFAULT_BACKEND as _DEFAULT_BACKEND

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"

# native 安装的 claude 默认位置（launchd 的 PATH 通常不含 ~/.local/bin）。
_CLAUDE_FALLBACK_BIN = Path.home() / ".local" / "bin" / "claude"
# native 安装的 agy 默认位置。
_AGY_FALLBACK_BIN = Path.home() / ".local" / "bin" / "agy"


def get_config_path(cli_args: list[str] | None = None) -> Path:
    """获取配置文件路径：优先命令行参数 --config / -c，其次环境变量 FEISHU_BRIDGE_CONFIG，默认 config.json。"""
    args = sys.argv[1:] if cli_args is None else cli_args
    for i, arg in enumerate(args):
        if arg in ("--config", "-c") and i + 1 < len(args):
            return Path(args[i + 1]).resolve()
        if arg.startswith("--config="):
            return Path(arg.split("=", 1)[1]).resolve()
    env_cfg = os.environ.get("FEISHU_BRIDGE_CONFIG")
    if env_cfg:
        return Path(env_cfg).resolve()
    return CONFIG_PATH


def get_instance_name(config: dict, config_path: Path | None = None) -> str:
    """获取实例标识：config["instance"] > config.<instance>.json > 空字符串。"""
    if config.get("instance"):
        return str(config["instance"]).strip()
    if config_path:
        stem = config_path.stem
        if stem.startswith("config."):
            return stem.split(".", 1)[1].strip()
    return ""


def load_config(config_path: Path | None = None) -> dict:
    if config_path is None:
        config_path = get_config_path()
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    app_id = cfg.get("app_id", "")
    if app_id.startswith("cli_") and len(app_id) > 10:
        # 后端选择：默认 claude；未知值回落到默认（只记日志不退出——
        # launchd KeepAlive 会把启动失败变成崩溃重启循环）。
        backend = cfg.get("backend") or _DEFAULT_BACKEND
        if backend not in _BACKENDS:
            logging.getLogger("feishu_codex_bridge.config").warning(
                "未知的 backend 配置 %r，回落到 %s", backend, _DEFAULT_BACKEND
            )
            backend = _DEFAULT_BACKEND
        cfg["backend"] = backend
        # 各后端的可执行文件都解析（切换后端无需改解析逻辑）。
        cfg["dsh_bin"] = resolve_dsh_bin(cfg.get("dsh_bin"))
        cfg["claude_bin"] = resolve_claude_bin(cfg.get("claude_bin"))
        cfg["agy_bin"] = resolve_agy_bin(cfg.get("agy_bin"))
        if not isinstance(cfg.get("claude_env"), dict):
            cfg["claude_env"] = {}
        if not isinstance(cfg.get("agy_env"), dict):
            cfg["agy_env"] = {}
        _startup_selfcheck(cfg)
        return cfg
    raise SystemExit(f"{config_path.name} 中的 app_id 尚未填写，请先按 README 配置飞书应用。")


def resolve_dsh_bin(configured: str | None = None) -> str:
    """解析可用的 dsh 可执行文件路径。

    优先级：
      1. 配置里显式指定的路径（存在且可执行）→ 直接用；
      2. `which dsh`（PATH 上有 dsh）→ 用 PATH 上的；
      3. 扫描 ~/.npm/_npx/*/node_modules/.bin/dsh，取最新的一个。
    返回解析结果；都找不到时返回配置值（由调用方在报错中体现）。
    """
    if configured:
        p = Path(configured)
        if p.is_file() and os.access(p, os.X_OK):
            return configured
        logging.getLogger("feishu_codex_bridge.dsh").warning(
            "配置的 dsh_bin 不存在或不可执行: %s，尝试自动解析", configured
        )

    # PATH 上的 dsh（如全局安装或 npx 已在 PATH 中）
    found = shutil.which("dsh")
    if found:
        return found

    # 扫描 npx 缓存目录，取最新一个
    npx_root = Path.home() / ".npm" / "_npx"
    candidates = []
    try:
        if npx_root.is_dir():
            for entry in npx_root.iterdir():
                if not entry.is_dir():
                    continue
                bin_path = entry / "node_modules" / ".bin" / "dsh"
                if bin_path.is_file() and os.access(bin_path, os.X_OK):
                    candidates.append(bin_path)
    except OSError:
        pass
    if candidates:
        # 目录 mtime 最新者优先（npx 每次新建缓存目录）
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return str(candidates[0])

    return configured or "dsh"


def resolve_claude_bin(configured: str | None = None) -> str:
    """解析可用的 claude 可执行文件路径。

    优先级：
      1. 配置里显式指定的路径（存在且可执行）→ 直接用；
      2. `which claude`（PATH 上有 claude）→ 用 PATH 上的；
      3. ~/.local/bin/claude（native 安装的默认位置，launchd 的 PATH
         通常不含它）。
    返回解析结果；都找不到时返回配置值（由调用方在报错中体现）。
    """
    if configured:
        p = Path(configured)
        if p.is_file() and os.access(p, os.X_OK):
            return configured
        logging.getLogger("feishu_codex_bridge.config").warning(
            "配置的 claude_bin 不存在或不可执行: %s，尝试自动解析", configured
        )

    found = shutil.which("claude")
    if found:
        return found

    if _CLAUDE_FALLBACK_BIN.is_file() and os.access(_CLAUDE_FALLBACK_BIN, os.X_OK):
        return str(_CLAUDE_FALLBACK_BIN)

    return configured or "claude"


def resolve_agy_bin(configured: str | None = None) -> str:
    """解析可用的 agy 可执行文件路径。

    优先级：
      1. 配置里显式指定的路径（存在且可执行）→ 直接用；
      2. `which agy`（PATH 上有 agy）→ 用 PATH 上的；
      3. ~/.local/bin/agy（native 安装默认位置，launchd 的 PATH 通常不含它）。
    返回解析结果；都找不到时返回配置值。
    """
    if configured:
        p = Path(configured)
        if p.is_file() and os.access(p, os.X_OK):
            return configured
        logging.getLogger("feishu_codex_bridge.config").warning(
            "配置的 agy_bin 不存在或不可执行: %s，尝试自动解析", configured
        )

    found = shutil.which("agy")
    if found:
        return found

    if _AGY_FALLBACK_BIN.is_file() and os.access(_AGY_FALLBACK_BIN, os.X_OK):
        return str(_AGY_FALLBACK_BIN)

    return configured or "agy"


def _startup_selfcheck(cfg: dict) -> None:
    """启动自检：只记 ERROR 日志不退出，避免 launchd 崩溃重启循环。"""
    backend = cfg.get("backend")
    logger = logging.getLogger("feishu_codex_bridge.config")
    if backend == "claude":
        bin_path = cfg.get("claude_bin") or ""
        p = Path(bin_path)
        if not (p.is_file() and os.access(p, os.X_OK)):
            logger.error("claude_bin 不可执行（%s），claude 任务将无法运行", bin_path)
        env = cfg.get("claude_env") or {}
        if not (
            env.get("ANTHROPIC_AUTH_TOKEN")
            or env.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("ANTHROPIC_API_KEY")
        ):
            logger.error(
                "未配置 ANTHROPIC_AUTH_TOKEN（config.json 的 claude_env 或环境变量）。"
                "launchd 环境拿不到 zshrc 里的 token，claude 调用会认证失败"
            )
    elif backend == "agy":
        bin_path = cfg.get("agy_bin") or ""
        p = Path(bin_path)
        if not (p.is_file() and os.access(p, os.X_OK)):
            logger.error("agy_bin 不可执行（%s），agy 任务将无法运行", bin_path)


def setup_logging(config: dict, instance: str = "") -> None:
    default_log = f"bridge.{instance}.log" if instance else "bridge.log"
    log_file = config.get("log_file") or default_log
    log_path = BASE_DIR / log_file
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
