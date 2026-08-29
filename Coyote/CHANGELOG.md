# Changelog

This changelog records source/package-facing changes for the `Coyote/` project directory. Public desktop release versions are defined by `src/Coyote/app_version.py`; the BepInEx plugin assembly is independently versioned in `src/Coyote/Coyote.csproj`.

## Unreleased - source layout refactor

- Added `src/Coyote/coyote_app/` as the canonical desktop application package.
- Centralized extension installation order in `coyote_app/bootstrap.py`; `main.py` is now a thin entrypoint.
- Grouped feature code under `coyote_app/features/`, Qt UI under `coyote_app/ui/`, and visual-rule code under `coyote_app/visual_rules/`.
- Kept historical Python module names as small module-identity compatibility aliases rather than duplicate implementations.
- Moved BepInEx C# source into `Plugin/` and `Telemetry/` folders.
- Added `tools/validate_structure.py` and wired it into CI and the Windows portable builder.
- Removed the legacy Python custom-rule example and Python custom-rule development guide; visual node graphs are the active custom-rule system.
- Updated repository/user/developer documentation to match strict built-in/custom rule separation and the reorganized source tree.

## 0.0.3 - 2026-08-28

- Rebuilt the Windows x64 portable package from the current repository source.
- Included the latest PEAK telemetry/plugin changes since 0.0.2.
- Included the latest network-mode and Qt UI changes since 0.0.2.
- Standardized GPL-3.0 licensing and removed generated-template metadata ambiguity.
- Documented independent desktop/plugin/subsystem version domains and source boundaries.
- Added repository CI, security/contribution guidance, and reproducible release automation.

For user-facing release history, also use the GitHub Releases for this repository.
