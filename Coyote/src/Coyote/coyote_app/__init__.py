"""Coyote desktop application package.

The package contains first-party desktop composition, feature extensions, UI,
and the visual-rule engine. A few path-sensitive modules intentionally remain
at ``src/Coyote`` (backend, i18n, updater/version and relay identity) so existing
portable installations keep the same resource/config resolution semantics.
"""

__all__ = ["bootstrap"]
