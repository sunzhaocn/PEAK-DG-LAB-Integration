# Contributing

## Source ownership

Keep changes within one layer where possible:

- PEAK/BepInEx plugin: `Coyote/src/Coyote/Plugin/` and `Telemetry/`
- stable desktop core/path ownership: `backend.py`, `i18n.py`, `update_checker.py`, `relay_config.py`
- application composition: `Coyote/src/Coyote/coyote_app/bootstrap.py`
- feature extensions: `coyote_app/features/`
- Qt UI: `coyote_app/ui/qt.py`
- visual-rule engine/integration/policy: `coyote_app/visual_rules/`
- runtime resources: `language/`, `md/`, `official_relay.json`
- vendored DG-LAB server: `Coyote/dglab-websocket-server-main/`

Do not add implementation code to compatibility modules such as
`extended_features.py`, `ui_qt.py` or `visual_rules.py`; they intentionally alias
canonical package modules for older imports.

Do not mix an upstream vendor refresh with unrelated first-party application
refactors in the same commit.

## Path-sensitive modules

`backend.py`, `i18n.py`, `update_checker.py` and `relay_config.py` derive runtime
paths from their own file location. Moving them requires an explicit path/data
migration and portable-build testing. Do not relocate them merely for cosmetic
folder consistency.

## Extension hook contract

`coyote_app/bootstrap.py` owns installation order. Backend hooks are installed
before the final Qt module is imported; UI wrappers are installed afterwards.
Install functions must be idempotent and must retain any original function/object
before wrapping it.

If ordering changes, update `docs/ARCHITECTURE.md` and test startup explicitly.

## Visual custom rules

User-authored Python custom-rule files are no longer supported. Do not add `.py`
files under `Coyote/custom_rules/` and do not restore the old Python custom-rule
guide. Visual rules are declarative graphs handled by `coyote_app/visual_rules/`.

The official automatic-rule domain and visual custom-rule domain are separate;
policy changes belong in `visual_rules/policy.py`, not scattered through UI or
detector code.

## Local checks

Run the structure contract first:

```bash
python Coyote/tools/validate_structure.py
```

Then compile all Python sources:

```bash
python - <<'PY'
from pathlib import Path
import py_compile
for root in (Path('Coyote/src/Coyote'), Path('Coyote/tools')):
    for path in root.rglob('*.py'):
        py_compile.compile(str(path), doraise=True)
print('Python compile OK')
PY
```

On Windows, run `Coyote/build_exe_selfcontained.ps1` for the complete C# +
PyInstaller portable build.

Run the offline runtime regressions with the desktop dependencies installed:

```bash
python -m pip install -r Coyote/requirements.txt
python -m unittest discover -s Coyote/tests -v
```

The tests install the real backend extensions in isolated processes, use temporary
configuration directories, disable real network I/O and replace device transport
with in-memory recorders. Qt tests use the offscreen platform. Add behavioral
regressions for output cancellation, device routing and rule/configuration
changes; do not use a real game or device connection in this test suite.

Do not claim PEAK or DG-LAB hardware testing unless it was actually performed.
