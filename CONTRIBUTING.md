# Contributing

## Scope

Keep changes attributable to one layer where possible:

- PEAK/BepInEx telemetry: `Coyote/src/Coyote/*.cs`
- desktop backend/rules: `Coyote/src/Coyote/backend.py`
- feature extensions: `extended_features.py`, `multiplayer_features.py`, `network_features.py`, `remote_reporting.py`
- UI: `ui_qt.py`
- vendored upstream DG-LAB server: `Coyote/dglab-websocket-server-main/`

Do not mix an upstream-vendor refresh with unrelated application refactors in the same commit.

## Extension hook contract

`main.py` intentionally installs backend hooks before importing/starting the Qt UI, then installs UI hooks. Extension installers must remain idempotent. If that ordering changes, update `docs/ARCHITECTURE.md` and test startup explicitly.

## Checks

Before opening a pull request:

```bash
python -m py_compile Coyote/src/Coyote/*.py
```

Also validate edited JSON files and, when possible, run the Windows portable builder. A full BepInEx plugin build may require PEAK/Unity assemblies or the configured fallback references.

Do not claim PEAK or DG-LAB hardware testing unless it was actually performed.
