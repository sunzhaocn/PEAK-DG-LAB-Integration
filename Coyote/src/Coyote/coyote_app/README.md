# `coyote_app` desktop package

This directory is the canonical home for first-party desktop composition and
feature code that is safe to package independently of filesystem location.

```text
coyote_app/
├─ bootstrap.py          # application composition / installation order
├─ features/
│  ├─ extended.py        # recovery, areas, random waveform, HP ramp
│  ├─ multiplayer.py     # multiplayer telemetry + multi-device routing
│  ├─ network.py         # direct / project relay / custom relay modes
│  └─ reporting.py       # optional encrypted diagnostics/reporting
├─ ui/
│  └─ qt.py              # PySide6 desktop UI
└─ visual_rules/
   ├─ engine.py          # graph storage, evaluator and graph editor
   ├─ integration.py     # existing detector/output integration
   └─ policy.py          # built-in/custom rule separation policy
```

## Stable root modules

The following modules intentionally remain one directory above this package:

- `backend.py` — owns `ROOT`/`SOURCE_DIR`, config paths and shared runtime state;
- `i18n.py` — resolves `language/` relative to its stable source location;
- `update_checker.py` — resolves the source/install root and restart entrypoint;
- `app_version.py` — read directly by release automation;
- `relay_config.py` + `official_relay.json` — stable relay identity/resource path.

Moving those files without an explicit path migration would change runtime
behavior. New code should treat them as stable infrastructure rather than
silently relocating them.

## Compatibility modules

Historical imports such as `extended_features`, `ui_qt` and `visual_rules` are
kept as tiny module aliases. They point to the canonical package module using
`sys.modules`, so mutable module state and monkey-patched functions are not
copied into a second module object.

Do not add new implementation code to the compatibility files.
