"""配置加载与日志初始化。"""

import json
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    app_id = cfg.get("app_id", "")
    if app_id.startswith("cli_") and len(app_id) > 10:
        return cfg
    raise SystemExit("config.json 中的 app_id 尚未填写，请先按 README 配置飞书应用。")


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
