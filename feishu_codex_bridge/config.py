"""配置加载与日志初始化。"""

import json
import logging
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    app_id = cfg.get("app_id", "")
    if app_id.startswith("cli_") and len(app_id) > 10:
        # dsh_bin 动态解析：配置路径失效时自动找到当前可用的 dsh。
        cfg["dsh_bin"] = resolve_dsh_bin(cfg.get("dsh_bin"))
        return cfg
    raise SystemExit("config.json 中的 app_id 尚未填写，请先按 README 配置飞书应用。")


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
    from shutil import which

    found = which("dsh")
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


def setup_logging(config: dict) -> None:
    log_path = BASE_DIR / config.get("log_file", "bridge.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
