# Coyote

Coyote is a PEAK BepInEx plugin with a companion Windows GUI used to receive game telemetry and apply configured DG-LAB stimulation rules.

## Local build

Run `build_exe_selfcontained.bat` on Windows.

The EXE builder compiles `src/Coyote/Coyote.csproj` first, with Thunderstore packaging disabled for this local portable build, then packages the Python GUI and copies the freshly built `Coyote.dll` into `plugin/`.

## Project files

- `src/Coyote/Plugin.cs`: PEAK/BepInEx plugin telemetry.
- `src/Coyote/backend.py`: rule engine, DG-LAB control, logging.
- `src/Coyote/ui_qt.py`: Qt interface.
- `build_exe_selfcontained.bat`: Windows build entry point.
