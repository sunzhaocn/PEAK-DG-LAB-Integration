"""Static repository-layout checks used by CI and the Windows builder."""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "Coyote"

CANONICAL_FILES = (
    SOURCE_ROOT / "main.py",
    SOURCE_ROOT / "backend.py",
    SOURCE_ROOT / "i18n.py",
    SOURCE_ROOT / "update_checker.py",
    SOURCE_ROOT / "app_version.py",
    SOURCE_ROOT / "relay_config.py",
    SOURCE_ROOT / "official_relay.json",
    SOURCE_ROOT / "coyote_app" / "bootstrap.py",
    SOURCE_ROOT / "coyote_app" / "features" / "extended.py",
    SOURCE_ROOT / "coyote_app" / "features" / "multiplayer.py",
    SOURCE_ROOT / "coyote_app" / "features" / "network.py",
    SOURCE_ROOT / "coyote_app" / "features" / "reporting.py",
    SOURCE_ROOT / "coyote_app" / "ui" / "qt.py",
    SOURCE_ROOT / "coyote_app" / "visual_rules" / "engine.py",
    SOURCE_ROOT / "coyote_app" / "visual_rules" / "integration.py",
    SOURCE_ROOT / "coyote_app" / "visual_rules" / "policy.py",
    SOURCE_ROOT / "Plugin" / "CoyotePlugin.cs",
    SOURCE_ROOT / "Telemetry" / "MultiplayerTelemetry.cs",
)

COMPATIBILITY_FILES = (
    SOURCE_ROOT / "extended_features.py",
    SOURCE_ROOT / "multiplayer_features.py",
    SOURCE_ROOT / "network_features.py",
    SOURCE_ROOT / "remote_reporting.py",
    SOURCE_ROOT / "ui_qt.py",
    SOURCE_ROOT / "visual_rules.py",
    SOURCE_ROOT / "visual_rules_hardening.py",
    SOURCE_ROOT / "visual_rules_separation.py",
)

FORBIDDEN_LEGACY_FILES = (
    SOURCE_ROOT / "Plugin.cs",
    SOURCE_ROOT / "MultiplayerTelemetry.cs",
    SOURCE_ROOT / "md" / "自定义规则开发指南.md",
    PROJECT_ROOT / "custom_rules" / "example_speed_climb.py",
)


def fail(message: str) -> None:
    raise SystemExit(f"structure check failed: {message}")


def main() -> int:
    missing = [path for path in CANONICAL_FILES if not path.is_file()]
    if missing:
        fail("missing canonical files: " + ", ".join(str(p.relative_to(PROJECT_ROOT)) for p in missing))

    leftovers = [path for path in FORBIDDEN_LEGACY_FILES if path.exists()]
    if leftovers:
        fail("legacy files must be removed: " + ", ".join(str(p.relative_to(PROJECT_ROOT)) for p in leftovers))

    custom_rules = PROJECT_ROOT / "custom_rules"
    if custom_rules.exists() and any(custom_rules.rglob("*.py")):
        fail("Python custom-rule files are no longer part of the active rule system")

    for path in COMPATIBILITY_FILES:
        if not path.is_file():
            fail(f"missing compatibility module: {path.relative_to(PROJECT_ROOT)}")
        text = path.read_text(encoding="utf-8-sig")
        if len(text.encode("utf-8")) > 1600:
            fail(f"compatibility module contains implementation code: {path.relative_to(PROJECT_ROOT)}")
        if "sys.modules[__name__]" not in text:
            fail(f"compatibility module must alias the canonical module: {path.relative_to(PROJECT_ROOT)}")

    main_text = (SOURCE_ROOT / "main.py").read_text(encoding="utf-8-sig")
    if "from coyote_app.bootstrap import run" not in main_text:
        fail("main.py must delegate to coyote_app.bootstrap")

    relay = json.loads((SOURCE_ROOT / "official_relay.json").read_text(encoding="utf-8-sig"))
    if not isinstance(relay, dict) or not str(relay.get("url", "")).startswith("wss://"):
        fail("official_relay.json must contain a wss:// URL")

    print(
        "structure OK: "
        f"{len(CANONICAL_FILES)} canonical files, "
        f"{len(COMPATIBILITY_FILES)} compatibility aliases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
