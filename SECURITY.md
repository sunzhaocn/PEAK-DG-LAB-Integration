# Security policy

## Supported source

Security fixes target the current `main` branch and the latest published Coyote release.

## Reporting

Do not publish private keys, account credentials, relay administrator credentials, room/controller identifiers, private logs, or other sensitive runtime data in a public issue. For a suspected vulnerability, provide the minimum reproducible details and redact personal or deployment-specific data.

## Important trust boundaries

- PEAK telemetry is expected to originate from the local BepInEx plugin and is transported to the desktop process over the configured local UDP endpoint by default.
- Public/custom relay mode is required to use `wss://` by the client network layer.
- Custom Python rules are parsed and constrained by the application's AST validator and restricted builtins. This is an application-level restriction, **not an operating-system sandbox**. Only load rule files you trust.
- The vendored DG-LAB WebSocket server is a separate upstream-derived component. Keep its license/provenance intact when updating it.
- Update packages are downloaded from this repository's GitHub Releases over HTTPS and are path/size checked before extraction; release publishing credentials therefore remain a critical trust boundary.

Hardware output must still be tested at low intensity. Software limits and disconnect handling reduce risk but do not replace device-side limits or physical disconnection.
