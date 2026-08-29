# Security policy

## Supported source

Security fixes target the current `main` branch and the latest published Coyote release.

## Reporting

Do not publish private keys, account credentials, relay administrator credentials, room/controller identifiers, private logs, or other sensitive runtime data in a public issue. For a suspected vulnerability, provide the minimum reproducible details and redact personal or deployment-specific data.

## Important trust boundaries

- PEAK telemetry is expected to originate from the local BepInEx plugin and is transported to the desktop process over the configured local endpoint by default.
- Public/custom relay mode is required to use `wss://` by the client network layer.
- Visual custom rules are declarative graph data (`visual_rules.json`); the active custom-rule system does not execute user-authored Python files.
- Official automatic rules and visual custom rules are separate policy domains. Disabling built-in rules also disables their built-in death/passed-out rule-level protection; custom graphs that need equivalent conditions must include the explicit custom guard nodes.
- The master output switch, intensity hard limits, explicit stop/disconnect behavior and device routing remain shared infrastructure.
- The vendored DG-LAB WebSocket server is a separate upstream-derived component. Keep its license/provenance intact when updating it.
- Update packages are downloaded from this repository's GitHub Releases over HTTPS and are path/size checked before extraction; release publishing credentials therefore remain a critical trust boundary.

Hardware output should still be tested at low intensity. Software limits and disconnect handling reduce risk but do not replace device-side limits or physical disconnection.
