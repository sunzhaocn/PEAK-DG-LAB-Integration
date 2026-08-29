# Architecture

This document describes the source boundaries after the project-wide layout
refactor. The goal is to make ownership obvious without breaking runtime paths
that existing portable installations rely on.

## 1. Runtime boundaries

### PEAK / BepInEx plugin

```text
Coyote/src/Coyote/Plugin/CoyotePlugin.cs
Coyote/src/Coyote/Telemetry/MultiplayerTelemetry.cs
```

These files run inside PEAK through BepInEx. They collect local/multiplayer
telemetry and send it to the desktop application. They must not depend on the
Python desktop package.

### Desktop application

```text
Coyote/src/Coyote/main.py
        ↓
coyote_app/bootstrap.py
        ├─ features/*
        ├─ visual_rules/*
        └─ ui/qt.py
```

`main.py` is deliberately tiny. `coyote_app/bootstrap.py` is the **only
composition root** and owns extension installation order.

### DG-LAB transport

Direct mode uses the vendored Bun WebSocket server under:

```text
Coyote/dglab-websocket-server-main/
```

Relay modes connect the desktop controller to an explicitly selected WSS
endpoint. The vendored server is a third-party/upstream-derived subtree and
should not be mixed into unrelated first-party refactors.

## 2. Desktop source layout

```text
src/Coyote/
├─ backend.py                     # stable shared runtime core
├─ i18n.py                        # stable language locator
├─ update_checker.py              # stable updater/install-root locator
├─ app_version.py                 # release automation contract
├─ relay_config.py                # stable relay resource locator
├─ official_relay.json
├─ coyote_app/
│  ├─ bootstrap.py
│  ├─ features/
│  │  ├─ extended.py
│  │  ├─ multiplayer.py
│  │  ├─ network.py
│  │  └─ reporting.py
│  ├─ ui/
│  │  └─ qt.py
│  └─ visual_rules/
│     ├─ engine.py
│     ├─ integration.py
│     └─ policy.py
├─ language/
└─ md/
```

### Why some modules remain at `src/Coyote`

`backend.py`, `i18n.py`, `update_checker.py` and `relay_config.py` use their own
`__file__` location to resolve the project/install root or neighboring runtime
resources. Moving those modules without a migration layer would silently change
configuration, language, update or relay paths. They are therefore treated as
**stable infrastructure modules**, not as failed leftovers from the refactor.

## 3. Compatibility-import contract

Historical imports remain valid:

```text
extended_features.py
multiplayer_features.py
network_features.py
remote_reporting.py
ui_qt.py
visual_rules.py
visual_rules_hardening.py
visual_rules_separation.py
```

Those files contain no implementation. Each aliases the canonical package
module through `sys.modules[__name__]`.

This detail matters because the project intentionally wraps functions and
mutates module-level runtime state. A normal `from package import *` shim would
create a second module namespace and could split monkey-patched state.

**Rule:** new implementation code belongs only under `coyote_app/`; compatibility
files must stay small aliases.

## 4. Extension installation order

Backend hooks are installed before importing the final Qt UI:

1. `features.extended.install_backend`
2. `features.multiplayer.install_backend`
3. `features.network.install_backend`
4. `features.reporting.install_backend`
5. `visual_rules.engine.install_backend`
6. `visual_rules.integration.install`
7. `visual_rules.policy.install`
8. import `ui.qt`
9. install UI extensions in the corresponding order, with updater integration
   before the visual editor wrappers
10. call `ui.qt.main()`

Installers must remain idempotent and retain references to wrapped functions
before replacing them.

## 5. Visual-rule layers

### `visual_rules/engine.py`

Owns graph persistence, validation primitives, runtime evaluation, graph output
and the base Qt node editor.

### `visual_rules/integration.py`

Connects visual trigger nodes to existing Coyote detector parameters and output
modifiers (thresholds, areas, recovery detection, repeat cooldowns, HP ramp,
etc.). It should adapt existing capabilities rather than redefine graph policy.

### `visual_rules/policy.py`

Owns the product rule-domain policy:

- official automatic rules and visual custom rules are separate systems;
- `disable_builtin` disables the complete official automatic-rule domain;
- custom rules do not implicitly inherit official death/passed-out protection;
- custom death/passed-out protection must be expressed by custom guard nodes;
- built-in and custom death/passed-out triggers may coexist when built-ins are
  not disabled.

Keeping policy in one layer prevents safety/domain semantics from being spread
across unrelated editor and detector code.

## 6. Rule-file model

User-authored Python rule files are no longer an active feature and are not
shipped. Visual rules are declarative graph data stored in `visual_rules.json`.
The graph file does not execute user Python code.

## 7. Shared output infrastructure

Rule domains are separate, but these facilities remain shared infrastructure:

- DG-LAB controller connection and slot routing;
- master output switch;
- configured intensity hard limits;
- explicit stop/disconnect/application-shutdown cleanup;
- logging and device protocol helpers.

A feature module should not create a second uncontrolled transport stack when an
existing backend/device API can be reused.

## 8. Resources and vendored code

- `language/`: runtime locale JSON files; path is owned by `i18n.py`.
- `md/`: user documentation copied into the portable build.
- `official_relay.json`: project relay identity copied beside the portable EXE.
- `dglab-websocket-server-main/`: vendored upstream-derived transport server.
- `reference-stubs/`: declaration-only build references; never ship in release.

## 9. Automated structure contract

`Coyote/tools/validate_structure.py` is run by CI and the Windows builder. It
checks canonical paths, compatibility aliases, C# source moves and absence of
legacy Python custom-rule files.

When changing architecture, update the validator and this document in the same
change.
