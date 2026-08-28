# Coyote

Coyote is the PEAK-side plugin and companion Windows desktop application used by **PEAK-DG-LAB-Integration** to receive game telemetry and apply configured DG-LAB output rules.

The repository is an unofficial community project and is not affiliated with PEAK, DG-LAB, BepInEx or Thunderstore.

## Source boundaries

- `src/Coyote/Plugin.cs` and `MultiplayerTelemetry.cs`: PEAK/BepInEx telemetry plugin.
- `src/Coyote/backend.py`: shared rule engine, DG-LAB operations, configuration and logging.
- `src/Coyote/*_features.py`: extension layers installed from `main.py`.
- `src/Coyote/ui_qt.py`: Qt desktop UI.
- `custom_rules/`: user-authored rules loaded through the constrained rule interface.
- `dglab-websocket-server-main/`: vendored upstream-derived DG-LAB WebSocket server; see the repository-root `NOTICE.md`.

## Versions

The desktop application version and BepInEx plugin version are independent by design. See `../docs/VERSIONING.md` before changing either number.

## Local build

Run `build_exe_selfcontained.bat` on Windows. The builder compiles `src/Coyote/Coyote.csproj` with Thunderstore packaging disabled for the portable build, packages the Python GUI, and copies the newly built `Coyote.dll` into the portable output.

Thunderstore publishing is intentionally **disabled by default** in source metadata. A maintainer who wants to publish must explicitly configure the local team metadata and opt in.

## License

Coyote is distributed under GPL-3.0. `LICENSE` in this directory intentionally matches the repository-root GPL license.
