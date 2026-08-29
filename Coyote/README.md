# Coyote project directory

`Coyote/` contains the PEAK/BepInEx plugin, Windows desktop application, runtime
resources, build tooling and the vendored DG-LAB-compatible WebSocket server.

## Source map

```text
src/Coyote/
├─ Plugin/CoyotePlugin.cs
├─ Telemetry/MultiplayerTelemetry.cs
├─ main.py
├─ backend.py
├─ i18n.py
├─ update_checker.py
├─ relay_config.py
├─ coyote_app/
│  ├─ bootstrap.py
│  ├─ features/
│  ├─ ui/
│  └─ visual_rules/
├─ language/
└─ md/
```

The Python files still present at names such as `ui_qt.py`,
`extended_features.py` and `visual_rules.py` are compatibility aliases only.
Canonical implementation lives under `coyote_app/`.

`backend.py`, `i18n.py`, `update_checker.py`, `app_version.py`, `relay_config.py`
and `official_relay.json` intentionally keep stable paths because runtime or
release logic resolves resources relative to those locations.

## Custom rules

Coyote no longer ships or loads user-authored Python custom-rule files as the
active rule system. Users create visual node graphs in the desktop UI; graph data
is stored in `visual_rules.json` at runtime.

## Local build

On Windows run:

```text
build_exe_selfcontained.bat
```

or directly:

```powershell
.\build_exe_selfcontained.ps1
```

The builder validates the repository structure, compiles the C# plugin, compiles
all Python sources, runs tests when present, packages the Qt desktop app and
creates `release/Coyote_Windows_x64_Portable.zip`.

## Structure validation

```bash
python tools/validate_structure.py
```

This check enforces canonical module locations, compatibility shims, the C#
directory layout and removal of legacy Python custom-rule files.

## Versions

Desktop application and BepInEx plugin versions are independent by design. See
`../docs/VERSIONING.md` before changing version numbers.

## Third-party boundary

`dglab-websocket-server-main/` is an upstream-derived/compatible vendored
subtree. Keep its license and treat upstream refreshes separately from Coyote
application refactors. See repository-root `NOTICE.md`.

## License

Coyote is distributed under GPL-3.0. This directory's `LICENSE` intentionally
matches the repository-root license.
