"""Config + .env loading."""

from __future__ import annotations

import json
import os
from pathlib import Path

WORKBENCH_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = WORKBENCH_DIR / "config.json"


def load_env(path: Path | None = None) -> dict:
    """Read a .env file (KEY=VALUE, # comments) without extra dependencies."""
    env = dict(os.environ)
    env_file = path or (WORKBENCH_DIR / ".env")
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            env.setdefault(key, value)
    return env


def load_config(path: Path | None = None, env: dict | None = None) -> dict:
    config_path = path or DEFAULT_CONFIG
    if not config_path.is_file():
        raise FileNotFoundError(f"找不到配置文件：{config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    env = env or load_env()

    skill_root = Path(str(config.get("skill_root", "../apparel-style-recolor")))
    if not skill_root.is_absolute():
        candidate = (config_path.parent / skill_root).resolve()
        if not (candidate / "SKILL.md").is_file():
            alternative = (WORKBENCH_DIR / skill_root).resolve()
            if (alternative / "SKILL.md").is_file():
                candidate = alternative
        skill_root = candidate
    config["skill_root"] = str(skill_root)

    # 云平台挂载持久卷时，用环境变量指过去（例如 /data）
    data_dir = Path(str(env.get("WORKBENCH_DATA_DIR") or config.get("data_dir", "data")))
    if not data_dir.is_absolute():
        data_dir = (config_path.parent / data_dir).resolve()
    config["data_dir"] = str(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    config.setdefault("server", {})
    config.setdefault("defaults", {})
    config.setdefault("providers", {})
    config["env"] = env
    return config
