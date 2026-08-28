# Architecture

Coyote consists of three distinct runtime boundaries.

## 1. PEAK plugin

`Coyote/src/Coyote/Plugin.cs` and `MultiplayerTelemetry.cs` run inside PEAK through BepInEx. They collect game telemetry and send it to the desktop application. The default telemetry destination is local-only.

## 2. Desktop application

`main.py` is the composition root. The installation order is deliberate:

1. import feature modules;
2. call `install_backend()` for extensions;
3. import `ui_qt`;
4. call each extension's `install_ui()`;
5. run `ui_qt.main()`.

Several feature modules wrap selected functions/objects from `backend.py` and `ui_qt.py`. These are intentional extension hooks, not independent alternate implementations. Install functions must be idempotent and retain references to original functions before wrapping them.

`backend.py` remains the authoritative location for shared runtime state, rule evaluation, device operations and configuration used by the extensions.

## 3. DG-LAB transport

Direct mode starts/uses the vendored Bun WebSocket server under `Coyote/dglab-websocket-server-main/`. Relay modes connect the desktop controller to a WSS endpoint instead.

The vendored server is an upstream-derived third-party subtree. Do not silently edit it as though it were first-party Coyote code; record upstream refreshes separately.

## Custom-rule execution

Custom Python rules are loaded by the backend after AST validation and with restricted builtins. The restriction is intended to prevent ordinary rules from directly bypassing the backend API, but it must not be described as a hardened OS/process sandbox.

## Safety ownership

All device-output paths should converge on backend safety limits and stop/cleanup behavior. Feature modules should not create a separate uncontrolled device-operation path.
