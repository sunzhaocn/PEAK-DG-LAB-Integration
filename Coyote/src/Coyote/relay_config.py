"""Official relay identity.

Release builders should ship an official_relay.json next to this file.
That file is intentionally separate from coyote_gui_config.json so the
"Restore official relay" button cannot be redirected by ordinary user settings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_OFFICIAL_RELAY_NAME = "北京官方中继"
DEFAULT_OFFICIAL_RELAY_URL = "wss://peak.hbsuzh.cn"


def _candidate_files() -> list[Path]:
    here = Path(__file__).resolve().parent
    candidates = [here / "official_relay.json"]
    # PyInstaller --onefile / frozen builds may place data next to the EXE.
    try:
        import sys
        if getattr(sys, "frozen", False):
            candidates.insert(0, Path(sys.executable).resolve().parent / "official_relay.json")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "official_relay.json")
    except Exception:
        pass
    return candidates


def official_relay() -> tuple[str, str]:
    env_url = str(os.environ.get("COYOTE_OFFICIAL_RELAY_URL", "") or "").strip()
    env_name = str(os.environ.get("COYOTE_OFFICIAL_RELAY_NAME", "") or "").strip()
    if env_url:
        return env_name or DEFAULT_OFFICIAL_RELAY_NAME, env_url

    for path in _candidate_files():
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                continue
            url = str(data.get("url", "") or "").strip()
            name = str(data.get("name", "") or "").strip()
            if url:
                return name or DEFAULT_OFFICIAL_RELAY_NAME, url
        except Exception:
            continue

    return DEFAULT_OFFICIAL_RELAY_NAME, DEFAULT_OFFICIAL_RELAY_URL
